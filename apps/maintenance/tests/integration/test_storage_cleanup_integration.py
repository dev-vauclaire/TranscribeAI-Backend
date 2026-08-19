from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from io import BytesIO
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maintenance.storage_cleanup import (
    StorageCleanupAbortedError,
    StorageCleanupService,
)
from transcribe_ai_shared import (
    FileSystemAudioStorage,
    JobRepository,
    JobStatus,
    JobType,
    TranscriptionJob,
    async_transaction,
)


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 8, 18, 12, tzinfo=UTC)
GRACE_PERIOD = timedelta(hours=1)
OLD_TIMESTAMP = NOW - timedelta(hours=2)
RECENT_TERMINAL_TIMESTAMP = NOW - timedelta(minutes=30)


def create_old_audio(
    storage: FileSystemAudioStorage,
    root: Path,
    job_uuid: UUID,
) -> Path:
    """Crée un audio puis vieillit le fichier et son dossier pour le cleanup."""
    location = storage.save(job_uuid, BytesIO(b"audio"), extension="wav")
    audio_path = root / location.uri
    old_epoch = OLD_TIMESTAMP.timestamp()
    os.utime(audio_path, (old_epoch, old_epoch))
    os.utime(audio_path.parent, (old_epoch, old_epoch))
    return audio_path.parent


def make_job(
    job_uuid: UUID,
    status: JobStatus,
    *,
    completed_at: datetime | None = None,
) -> TranscriptionJob:
    values: dict[str, object] = {
        "job_uuid": job_uuid,
        "job_type": JobType.FAST,
        "status": status,
        "audio_uri": f"{job_uuid}/input.wav",
        "completed_at": completed_at,
    }
    if status is JobStatus.PROCESSING:
        values.update(
            lease_owner="worker-fast-integration",
            lease_expires_at=NOW + timedelta(minutes=5),
        )
    return TranscriptionJob(**values)


async def persist_jobs(
    session_factory: async_sessionmaker[AsyncSession],
    jobs: Collection[TranscriptionJob],
) -> None:
    async with async_transaction(session_factory) as session:
        repository = JobRepository(session)
        for job in jobs:
            await repository.add(job)


async def test_cleanup_applies_postgresql_policy_and_is_idempotent(
    async_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    storage = FileSystemAudioStorage(tmp_path)
    queued_uuid = uuid4()
    processing_uuid = uuid4()
    completed_uuid = uuid4()
    failed_uuid = uuid4()
    recent_terminal_uuid = uuid4()
    orphan_uuid = uuid4()
    job_uuids = {
        queued_uuid,
        processing_uuid,
        completed_uuid,
        failed_uuid,
        recent_terminal_uuid,
    }
    directories = {
        job_uuid: create_old_audio(storage, tmp_path, job_uuid)
        for job_uuid in (*job_uuids, orphan_uuid)
    }
    await persist_jobs(
        async_session_factory,
        [
            make_job(queued_uuid, JobStatus.QUEUED),
            make_job(processing_uuid, JobStatus.PROCESSING),
            make_job(
                completed_uuid,
                JobStatus.COMPLETED,
                completed_at=OLD_TIMESTAMP,
            ),
            make_job(
                failed_uuid,
                JobStatus.FAILED,
                completed_at=OLD_TIMESTAMP,
            ),
            make_job(
                recent_terminal_uuid,
                JobStatus.COMPLETED,
                completed_at=RECENT_TERMINAL_TIMESTAMP,
            ),
        ],
    )
    loaded_batches: list[set[UUID]] = []

    async def load_jobs(job_ids: Collection[UUID]) -> list[TranscriptionJob]:
        loaded_batches.append(set(job_ids))
        async with async_session_factory() as session:
            return await JobRepository(session).get_jobs_by_uuids(job_ids)

    service = StorageCleanupService(storage, load_jobs, GRACE_PERIOD)

    first_result = await service.cleanup(now=NOW)

    assert loaded_batches == [job_uuids | {orphan_uuid}]
    assert first_result.inspected_count == 6
    assert first_result.kept_count == 3
    assert first_result.deleted_count == 3
    assert first_result.orphan_deleted_count == 1
    assert first_result.too_recent_count == 0
    assert first_result.error_count == 0
    assert directories[queued_uuid].is_dir()
    assert directories[processing_uuid].is_dir()
    assert directories[recent_terminal_uuid].is_dir()
    assert not directories[completed_uuid].exists()
    assert not directories[failed_uuid].exists()
    assert not directories[orphan_uuid].exists()

    second_result = await service.cleanup(now=NOW)

    assert second_result.inspected_count == 3
    assert second_result.kept_count == 3
    assert second_result.deleted_count == 0
    assert second_result.orphan_deleted_count == 0
    assert second_result.error_count == 0
    assert directories[queued_uuid].is_dir()
    assert directories[processing_uuid].is_dir()
    assert directories[recent_terminal_uuid].is_dir()


async def test_cleanup_aborts_without_deleting_when_postgresql_lookup_fails(
    tmp_path: Path,
) -> None:
    storage = FileSystemAudioStorage(tmp_path)
    first_directory = create_old_audio(storage, tmp_path, uuid4())
    second_directory = create_old_audio(storage, tmp_path, uuid4())

    async def unavailable_postgresql(
        job_uuids: Collection[UUID],
    ) -> list[TranscriptionJob]:
        raise RuntimeError("PostgreSQL unavailable")

    service = StorageCleanupService(
        storage,
        unavailable_postgresql,
        GRACE_PERIOD,
    )

    with pytest.raises(StorageCleanupAbortedError):
        await service.cleanup(now=NOW)

    assert first_directory.is_dir()
    assert second_directory.is_dir()
