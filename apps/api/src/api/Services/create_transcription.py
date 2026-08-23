from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import logging
from typing import BinaryIO
from uuid import UUID, uuid4

from api.Media.models import AudioFormat, AudioMetadata
from api.Media.protocols import MediaProbe
from api.Services.protocols import JobRepositoryFactory
from api.exceptions import (
    AudioStorageUnavailableError,
    AudioTooLongError,
    InvalidAudioFileError,
    MediaProbeUnavailableError,
    StoredMediaValidationError,
    TranscriptionPersistenceError,
    UnsupportedAudioFormatError,
)
from transcribe_ai_shared import (
    AsyncSessionFactory,
    AudioLocation,
    AudioStorage,
    JobRepository,
    JobStatus,
    JobType,
    TranscriptionJob,
    async_transaction,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CreateTranscriptionResult:
    """Résultat applicatif indépendant du modèle ORM."""

    job_uuid: UUID
    status: JobStatus


class CreateTranscriptionService:
    """Orchestre stockage, inspection média et persistance du job."""

    def __init__(
        self,
        *,
        storage: AudioStorage,
        media_probe: MediaProbe,
        session_factory: AsyncSessionFactory,
        fast_max_duration_seconds: Decimal,
        long_form_diarization_max_duration_seconds: Decimal,
        repository_factory: JobRepositoryFactory = JobRepository,
    ) -> None:
        if (
            not isinstance(fast_max_duration_seconds, Decimal)
            or not isinstance(long_form_diarization_max_duration_seconds, Decimal)
            or not fast_max_duration_seconds.is_finite()
            or not long_form_diarization_max_duration_seconds.is_finite()
            or fast_max_duration_seconds <= 0
            or long_form_diarization_max_duration_seconds <= 0
        ):
            raise ValueError("Les limites de durée doivent être positives et finies.")
        if fast_max_duration_seconds > long_form_diarization_max_duration_seconds:
            raise ValueError(
                "La limite FAST ne peut pas dépasser la limite LONG_FORM_DIARIZATION."
            )
        self._storage = storage
        self._media_probe = media_probe
        self._session_factory = session_factory
        self._duration_limits = {
            JobType.FAST: fast_max_duration_seconds,
            JobType.LONG_FORM_DIARIZATION: long_form_diarization_max_duration_seconds,
        }
        self._repository_factory = repository_factory

    async def create(
        self,
        *,
        source: BinaryIO,
        extension: str,
        job_type: JobType,
    ) -> CreateTranscriptionResult:
        """Crée un job durable et compense les fichiers devenus orphelins."""
        job_uuid = uuid4()
        try:
            location = await asyncio.to_thread(
                self._storage.save,
                job_uuid,
                source,
                extension=extension,
            )
        except Exception as error:
            raise AudioStorageUnavailableError(
                "Le stockage audio est indisponible."
            ) from error

        try:
            metadata = await asyncio.to_thread(
                self._probe_stored_audio,
                location,
            )
            self._validate_media_policy(
                metadata_format=metadata.format,
                duration_seconds=metadata.duration_seconds,
                declared_extension=extension,
                job_type=job_type,
            )
        except (
            AudioStorageUnavailableError,
            MediaProbeUnavailableError,
            StoredMediaValidationError,
        ):
            await self._delete_after_failure(location)
            raise
        except Exception as error:
            await self._delete_after_failure(location)
            raise MediaProbeUnavailableError(
                "Le service d'inspection audio est indisponible."
            ) from error

        repository_add_completed = False
        try:
            job = TranscriptionJob(
                job_uuid=job_uuid,
                status=JobStatus.QUEUED,
                job_type=job_type,
                audio_uri=location.uri,
                dispatch_required=True,
            )
            async with async_transaction(self._session_factory) as session:
                repository = self._repository_factory(session)
                await repository.add(job)
                repository_add_completed = True
        except Exception as error:
            if repository_add_completed:
                persisted = await self._job_exists_after_commit_error(job_uuid)
                if persisted is True:
                    return CreateTranscriptionResult(
                        job_uuid=job_uuid,
                        status=JobStatus.QUEUED,
                    )
            else:
                await self._delete_after_failure(location)
            raise TranscriptionPersistenceError(
                "Le job de transcription n'a pas pu être enregistré."
            ) from error

        return CreateTranscriptionResult(
            job_uuid=job_uuid,
            status=JobStatus.QUEUED,
        )

    def _probe_stored_audio(self, location: AudioLocation) -> AudioMetadata:
        """Distingue une panne de lecture du stockage d'une erreur média."""
        try:
            with self._storage.open(location) as stored_audio:
                try:
                    return self._media_probe.probe(stored_audio)
                except (MediaProbeUnavailableError, StoredMediaValidationError):
                    raise
                except Exception as error:
                    raise MediaProbeUnavailableError(
                        "Le service d'inspection audio est indisponible."
                    ) from error
        except (MediaProbeUnavailableError, StoredMediaValidationError):
            raise
        except Exception as error:
            raise AudioStorageUnavailableError(
                "Le fichier stocké ne peut pas être relu."
            ) from error

    def _validate_media_policy(
        self,
        *,
        metadata_format: AudioFormat,
        duration_seconds: Decimal,
        declared_extension: str,
        job_type: JobType,
    ) -> None:
        """Applique l'authenticité du format et la durée propre au use case."""
        if not duration_seconds.is_finite() or duration_seconds <= 0:
            raise InvalidAudioFileError("La durée du fichier audio est invalide.")
        if metadata_format.value != declared_extension:
            raise UnsupportedAudioFormatError(
                "Le contenu audio ne correspond pas à son extension déclarée."
            )
        max_duration = self._duration_limits[job_type]
        if duration_seconds > max_duration:
            raise AudioTooLongError(
                actual_duration_seconds=duration_seconds,
                max_duration_seconds=max_duration,
                job_type=job_type,
            )

    async def _delete_after_failure(self, location: AudioLocation) -> None:
        """Préserve l'erreur initiale même si la compensation échoue."""
        try:
            await asyncio.to_thread(self._storage.delete, location)
        except Exception:
            logger.exception(
                "Impossible de supprimer l'audio orphelin du job %s.",
                location.job_uuid,
            )

    async def _job_exists_after_commit_error(self, job_uuid: UUID) -> bool | None:
        """Résout si possible une perte d'acquittement du COMMIT.

        Après un flush réussi, une erreur réseau ne prouve pas que PostgreSQL a
        annulé le COMMIT. En cas d'incertitude, l'audio est donc conservé pour
        éviter qu'un job durable référence un fichier supprimé.
        """
        try:
            async with self._session_factory() as session:
                repository = self._repository_factory(session)
                return (await repository.get_by_uuid(job_uuid)) is not None
        except Exception:
            logger.exception(
                "Impossible de vérifier le commit du job %s après une erreur.",
                job_uuid,
            )
            return None
