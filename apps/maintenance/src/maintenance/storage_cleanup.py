from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from uuid import UUID

from transcribe_ai_shared.database.models import JobStatus, TranscriptionJob
from transcribe_ai_shared.observability import log_event
from transcribe_ai_shared.storage import (
    AudioStorageMaintenance,
    TranscriptionDirectory,
)


JobLoader = Callable[[Collection[UUID]], Awaitable[list[TranscriptionJob]]]
LOGGER = logging.getLogger(__name__)
SERVICE_NAME = "maintenance"


class StorageCleanupAbortedError(RuntimeError):
    """Erreur globale imposant l'abandon du cleanup avant toute suppression."""

    def __init__(
        self,
        *,
        dependency: str,
        reason: str,
        error_type: str,
    ) -> None:
        super().__init__("Le cleanup a été interrompu par mesure de sécurité.")
        self.dependency = dependency
        self.reason = reason
        self.error_type = error_type


@dataclass(frozen=True, slots=True)
class StorageCleanupResult:
    """Résumé non sensible d'une exécution du cleanup audio.

    ``deleted_count`` inclut une cible déjà supprimée concurremment : l'état
    final attendu est atteint et l'opération reste idempotente.
    """

    inspected_count: int
    kept_count: int
    deleted_count: int
    orphan_deleted_count: int
    too_recent_count: int
    error_count: int


class StorageCleanupService:
    """Applique une politique conservatrice entre PostgreSQL et le stockage."""

    def __init__(
        self,
        storage: AudioStorageMaintenance,
        load_jobs: JobLoader,
        grace_period: timedelta,
    ) -> None:
        if grace_period <= timedelta(0):
            raise ValueError("grace_period doit être strictement positive")
        self._storage = storage
        self._load_jobs = load_jobs
        self._grace_period = grace_period

    async def cleanup(self, *, now: datetime | None = None) -> StorageCleanupResult:
        """Scanne, charge tous les jobs par lot, puis supprime les candidats sûrs."""
        current_time = now or datetime.now(UTC)
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            raise ValueError("now doit contenir un fuseau horaire")
        cutoff = current_time - self._grace_period

        try:
            scan = self._storage.scan_transcription_directories()
        except Exception as error:
            raise StorageCleanupAbortedError(
                dependency="filesystem",
                reason="scan_failed",
                error_type=type(error).__name__,
            ) from error

        inspected_count = (
            len(scan.directories)
            + scan.invalid_entry_count
            + scan.unsafe_entry_count
            + scan.error_count
        )
        kept_count = (
            scan.invalid_entry_count + scan.unsafe_entry_count + scan.error_count
        )
        error_count = kept_count
        too_recent_count = 0
        candidates: list[TranscriptionDirectory] = []

        for directory in scan.directories:
            if directory.modified_at >= cutoff:
                kept_count += 1
                too_recent_count += 1
            else:
                candidates.append(directory)

        if not candidates:
            return StorageCleanupResult(
                inspected_count=inspected_count,
                kept_count=kept_count,
                deleted_count=0,
                orphan_deleted_count=0,
                too_recent_count=too_recent_count,
                error_count=error_count,
            )

        # Toutes les lectures SQL doivent réussir avant la première suppression.
        try:
            jobs = await self._load_jobs(
                tuple(directory.job_uuid for directory in candidates)
            )
        except Exception as error:
            raise StorageCleanupAbortedError(
                dependency="postgresql",
                reason="job_lookup_failed",
                error_type=type(error).__name__,
            ) from error

        jobs_by_uuid = {job.job_uuid: job for job in jobs}
        deleted_count = 0
        orphan_deleted_count = 0

        for directory in candidates:
            job = jobs_by_uuid.get(directory.job_uuid)
            if job is None:
                deleted = self._delete_directory(directory, reason="orphan")
                if deleted:
                    deleted_count += 1
                    orphan_deleted_count += 1
                else:
                    kept_count += 1
                    error_count += 1
                continue

            if job.status in {JobStatus.QUEUED, JobStatus.PROCESSING}:
                kept_count += 1
                continue

            if job.status not in {JobStatus.COMPLETED, JobStatus.FAILED}:
                kept_count += 1
                error_count += 1
                log_event(
                    LOGGER,
                    logging.WARNING,
                    service=SERVICE_NAME,
                    event="cleanup",
                    job_uuid=directory.job_uuid,
                    attempt_count=job.attempt_count,
                    action="keep",
                    reason="unknown_status",
                )
                continue

            if not self._is_old_terminal_job(job, cutoff):
                kept_count += 1
                if job.completed_at is None or not self._is_aware(job.completed_at):
                    error_count += 1
                    log_event(
                        LOGGER,
                        logging.WARNING,
                        service=SERVICE_NAME,
                        event="cleanup",
                        job_uuid=directory.job_uuid,
                        attempt_count=job.attempt_count,
                        action="keep",
                        reason="missing_terminal_timestamp",
                    )
                continue

            if self._delete_directory(
                directory,
                reason=job.status.value.lower(),
                attempt_count=job.attempt_count,
            ):
                deleted_count += 1
            else:
                kept_count += 1
                error_count += 1

        return StorageCleanupResult(
            inspected_count=inspected_count,
            kept_count=kept_count,
            deleted_count=deleted_count,
            orphan_deleted_count=orphan_deleted_count,
            too_recent_count=too_recent_count,
            error_count=error_count,
        )

    def _delete_directory(
        self,
        directory: TranscriptionDirectory,
        *,
        reason: str,
        attempt_count: int | None = None,
    ) -> bool:
        """Isole une erreur locale afin de poursuivre avec les autres dossiers."""
        try:
            deleted = self._storage.delete_transcription_directory(directory)
        except Exception as error:
            log_event(
                LOGGER,
                logging.WARNING,
                service=SERVICE_NAME,
                event="cleanup",
                job_uuid=directory.job_uuid,
                attempt_count=attempt_count,
                action="keep",
                reason="delete_failed",
                source=reason,
                error_type=type(error).__name__,
            )
            return False

        if deleted:
            log_event(
                LOGGER,
                logging.INFO,
                service=SERVICE_NAME,
                event="cleanup",
                job_uuid=directory.job_uuid,
                attempt_count=attempt_count,
                action="delete",
                reason=reason,
            )
        else:
            log_event(
                LOGGER,
                logging.INFO,
                service=SERVICE_NAME,
                event="cleanup",
                job_uuid=directory.job_uuid,
                attempt_count=attempt_count,
                action="delete",
                reason="already_absent",
                source=reason,
            )
        # Une cible déjà absente satisfait l'opération idempotente de cleanup.
        return True

    @classmethod
    def _is_old_terminal_job(
        cls,
        job: TranscriptionJob,
        cutoff: datetime,
    ) -> bool:
        completed_at = job.completed_at
        return (
            completed_at is not None
            and cls._is_aware(completed_at)
            and completed_at < cutoff
        )

    @staticmethod
    def _is_aware(value: datetime) -> bool:
        return value.tzinfo is not None and value.utcoffset() is not None
