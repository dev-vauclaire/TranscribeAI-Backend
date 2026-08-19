from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from io import BytesIO
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from transcribe_ai_shared.database.models import (
    JobStatus,
    JobType,
    TranscriptionJob,
)
from maintenance.storage_cleanup import (
    StorageCleanupAbortedError,
    StorageCleanupResult,
    StorageCleanupService,
)
from transcribe_ai_shared.storage import (
    FileSystemAudioStorage,
    TranscriptionDirectory,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

NOW = datetime(2026, 8, 18, 12, tzinfo=UTC)
GRACE_PERIOD = timedelta(hours=1)
OLD_TIMESTAMP = NOW - timedelta(hours=2)
RECENT_TIMESTAMP = NOW - timedelta(minutes=30)
AUDIO_CONTENT = b"audio content"


class RecordingJobLoader:
    """Simule la lecture PostgreSQL et conserve les lots demandés."""

    def __init__(
        self,
        jobs: Collection[TranscriptionJob] = (),
        *,
        error: Exception | None = None,
    ) -> None:
        self._jobs = tuple(jobs)
        self._error = error
        self.calls: list[tuple[UUID, ...]] = []

    async def __call__(
        self,
        job_uuids: Collection[UUID],
    ) -> list[TranscriptionJob]:
        requested_job_uuids = tuple(job_uuids)
        self.calls.append(requested_job_uuids)
        if self._error is not None:
            raise self._error

        requested_set = set(requested_job_uuids)
        return [job for job in self._jobs if job.job_uuid in requested_set]


def make_job(
    job_uuid: UUID,
    status: JobStatus,
    *,
    completed_at: datetime | None = None,
) -> TranscriptionJob:
    return TranscriptionJob(
        job_uuid=job_uuid,
        status=status,
        job_type=JobType.FAST,
        audio_uri=f"{job_uuid}/input.wav",
        completed_at=completed_at,
    )


def create_audio_directory(
    storage: FileSystemAudioStorage,
    root: Path,
    job_uuid: UUID,
    *,
    modified_at: datetime,
) -> Path:
    location = storage.save(
        job_uuid,
        BytesIO(AUDIO_CONTENT),
        extension="wav",
    )
    audio_path = root / location.uri
    timestamp = modified_at.timestamp()
    os.utime(audio_path, (timestamp, timestamp))
    os.utime(audio_path.parent, (timestamp, timestamp))
    return audio_path.parent


def assert_cleanup_result(
    result: StorageCleanupResult,
    *,
    inspected: int,
    kept: int,
    deleted: int,
    orphan_deleted: int = 0,
    too_recent: int = 0,
    errors: int = 0,
) -> None:
    assert result == StorageCleanupResult(
        inspected_count=inspected,
        kept_count=kept,
        deleted_count=deleted,
        orphan_deleted_count=orphan_deleted,
        too_recent_count=too_recent,
        error_count=errors,
    )
    assert result.kept_count + result.deleted_count == result.inspected_count
    assert result.orphan_deleted_count <= result.deleted_count


async def test_recent_orphan_directory_is_kept_without_database_lookup(
    tmp_path: Path,
):
    storage = FileSystemAudioStorage(tmp_path)
    job_uuid = uuid4()
    directory = create_audio_directory(
        storage,
        tmp_path,
        job_uuid,
        modified_at=RECENT_TIMESTAMP,
    )
    loader = RecordingJobLoader()

    result = await StorageCleanupService(
        storage,
        loader,
        GRACE_PERIOD,
    ).cleanup(now=NOW)

    assert directory.is_dir()
    assert loader.calls == []
    assert_cleanup_result(
        result,
        inspected=1,
        kept=1,
        deleted=0,
        too_recent=1,
    )


async def test_directory_exactly_at_grace_cutoff_is_kept(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)
    directory = create_audio_directory(
        storage,
        tmp_path,
        uuid4(),
        modified_at=NOW - GRACE_PERIOD,
    )
    loader = RecordingJobLoader()

    result = await StorageCleanupService(
        storage,
        loader,
        GRACE_PERIOD,
    ).cleanup(now=NOW)

    assert directory.is_dir()
    assert loader.calls == []
    assert_cleanup_result(
        result,
        inspected=1,
        kept=1,
        deleted=0,
        too_recent=1,
    )


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.PROCESSING])
async def test_old_active_job_directory_is_kept(
    tmp_path: Path,
    status: JobStatus,
):
    storage = FileSystemAudioStorage(tmp_path)
    job_uuid = uuid4()
    directory = create_audio_directory(
        storage,
        tmp_path,
        job_uuid,
        modified_at=OLD_TIMESTAMP,
    )
    loader = RecordingJobLoader([make_job(job_uuid, status)])

    result = await StorageCleanupService(
        storage,
        loader,
        GRACE_PERIOD,
    ).cleanup(now=NOW)

    assert directory.is_dir()
    assert loader.calls == [(job_uuid,)]
    assert_cleanup_result(result, inspected=1, kept=1, deleted=0)


@pytest.mark.parametrize("status", [JobStatus.COMPLETED, JobStatus.FAILED])
async def test_old_terminal_job_directory_is_deleted(
    tmp_path: Path,
    status: JobStatus,
):
    storage = FileSystemAudioStorage(tmp_path)
    job_uuid = uuid4()
    directory = create_audio_directory(
        storage,
        tmp_path,
        job_uuid,
        modified_at=OLD_TIMESTAMP,
    )
    loader = RecordingJobLoader(
        [make_job(job_uuid, status, completed_at=OLD_TIMESTAMP)]
    )

    result = await StorageCleanupService(
        storage,
        loader,
        GRACE_PERIOD,
    ).cleanup(now=NOW)

    assert directory.exists() is False
    assert_cleanup_result(result, inspected=1, kept=0, deleted=1)


@pytest.mark.parametrize("status", [JobStatus.COMPLETED, JobStatus.FAILED])
async def test_terminal_job_with_recent_database_timestamp_is_kept(
    tmp_path: Path,
    status: JobStatus,
):
    storage = FileSystemAudioStorage(tmp_path)
    job_uuid = uuid4()
    directory = create_audio_directory(
        storage,
        tmp_path,
        job_uuid,
        modified_at=OLD_TIMESTAMP,
    )
    loader = RecordingJobLoader(
        [make_job(job_uuid, status, completed_at=RECENT_TIMESTAMP)]
    )

    result = await StorageCleanupService(
        storage,
        loader,
        GRACE_PERIOD,
    ).cleanup(now=NOW)

    assert directory.is_dir()
    assert_cleanup_result(result, inspected=1, kept=1, deleted=0)


async def test_terminal_job_exactly_at_grace_cutoff_is_kept(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)
    job_uuid = uuid4()
    directory = create_audio_directory(
        storage,
        tmp_path,
        job_uuid,
        modified_at=OLD_TIMESTAMP,
    )
    loader = RecordingJobLoader(
        [
            make_job(
                job_uuid,
                JobStatus.COMPLETED,
                completed_at=NOW - GRACE_PERIOD,
            )
        ]
    )

    result = await StorageCleanupService(
        storage,
        loader,
        GRACE_PERIOD,
    ).cleanup(now=NOW)

    assert directory.is_dir()
    assert_cleanup_result(result, inspected=1, kept=1, deleted=0)


@pytest.mark.parametrize("status", [JobStatus.COMPLETED, JobStatus.FAILED])
async def test_terminal_job_without_completion_timestamp_is_kept_as_an_error(
    tmp_path: Path,
    status: JobStatus,
):
    storage = FileSystemAudioStorage(tmp_path)
    job_uuid = uuid4()
    directory = create_audio_directory(
        storage,
        tmp_path,
        job_uuid,
        modified_at=OLD_TIMESTAMP,
    )
    loader = RecordingJobLoader([make_job(job_uuid, status)])

    result = await StorageCleanupService(
        storage,
        loader,
        GRACE_PERIOD,
    ).cleanup(now=NOW)

    assert directory.is_dir()
    assert_cleanup_result(
        result,
        inspected=1,
        kept=1,
        deleted=0,
        errors=1,
    )


async def test_old_orphan_directory_is_deleted(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)
    job_uuid = uuid4()
    directory = create_audio_directory(
        storage,
        tmp_path,
        job_uuid,
        modified_at=OLD_TIMESTAMP,
    )
    loader = RecordingJobLoader()

    result = await StorageCleanupService(
        storage,
        loader,
        GRACE_PERIOD,
    ).cleanup(now=NOW)

    assert directory.exists() is False
    assert_cleanup_result(
        result,
        inspected=1,
        kept=0,
        deleted=1,
        orphan_deleted=1,
    )


async def test_old_abandoned_upload_directory_is_deleted(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)
    job_uuid = uuid4()
    job_directory = tmp_path / str(job_uuid)
    job_directory.mkdir()
    temporary_audio = job_directory / f".upload-{'a' * 32}.tmp"
    temporary_audio.write_bytes(AUDIO_CONTENT)
    old_epoch = OLD_TIMESTAMP.timestamp()
    os.utime(temporary_audio, (old_epoch, old_epoch))
    os.utime(job_directory, (old_epoch, old_epoch))

    result = await StorageCleanupService(
        storage,
        RecordingJobLoader(),
        GRACE_PERIOD,
    ).cleanup(now=NOW)

    assert job_directory.exists() is False
    assert_cleanup_result(
        result,
        inspected=1,
        kept=0,
        deleted=1,
        orphan_deleted=1,
    )


async def test_invalid_uuid_directory_is_kept_and_reported_as_an_error(
    tmp_path: Path,
):
    storage = FileSystemAudioStorage(tmp_path)
    invalid_directory = tmp_path / "not-a-job-uuid"
    invalid_directory.mkdir()
    (invalid_directory / "input.wav").write_bytes(AUDIO_CONTENT)
    loader = RecordingJobLoader()

    result = await StorageCleanupService(
        storage,
        loader,
        GRACE_PERIOD,
    ).cleanup(now=NOW)

    assert invalid_directory.is_dir()
    assert loader.calls == []
    assert_cleanup_result(
        result,
        inspected=1,
        kept=1,
        deleted=0,
        errors=1,
    )


async def test_symlinked_uuid_directory_is_never_followed_or_deleted(tmp_path: Path):
    root = tmp_path / "storage"
    storage = FileSystemAudioStorage(root)
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    outside_audio = outside_directory / "input.wav"
    outside_audio.write_bytes(b"must be preserved")
    (root / str(uuid4())).symlink_to(outside_directory, target_is_directory=True)
    loader = RecordingJobLoader()

    result = await StorageCleanupService(
        storage,
        loader,
        GRACE_PERIOD,
    ).cleanup(now=NOW)

    assert outside_audio.read_bytes() == b"must be preserved"
    assert loader.calls == []
    assert_cleanup_result(
        result,
        inspected=1,
        kept=1,
        deleted=0,
        errors=1,
    )


async def test_local_delete_error_keeps_the_directory_and_cleanup_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    storage = FileSystemAudioStorage(tmp_path)
    failing_job_uuid = uuid4()
    deleted_job_uuid = uuid4()
    failing_directory = create_audio_directory(
        storage,
        tmp_path,
        failing_job_uuid,
        modified_at=OLD_TIMESTAMP,
    )
    deleted_directory = create_audio_directory(
        storage,
        tmp_path,
        deleted_job_uuid,
        modified_at=OLD_TIMESTAMP,
    )
    real_delete = storage.delete_transcription_directory

    def delete_with_local_failure(directory: TranscriptionDirectory) -> bool:
        if directory.job_uuid == failing_job_uuid:
            raise OSError("local storage failure")
        return real_delete(directory)

    monkeypatch.setattr(
        storage,
        "delete_transcription_directory",
        delete_with_local_failure,
    )

    result = await StorageCleanupService(
        storage,
        RecordingJobLoader(),
        GRACE_PERIOD,
    ).cleanup(now=NOW)

    assert failing_directory.is_dir()
    assert deleted_directory.exists() is False
    assert_cleanup_result(
        result,
        inspected=2,
        kept=1,
        deleted=1,
        orphan_deleted=1,
        errors=1,
    )


async def test_concurrently_removed_directory_is_an_idempotent_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    storage = FileSystemAudioStorage(tmp_path)
    directory = create_audio_directory(
        storage,
        tmp_path,
        uuid4(),
        modified_at=OLD_TIMESTAMP,
    )
    real_delete = storage.delete_transcription_directory

    def delete_then_report_already_absent(
        candidate: TranscriptionDirectory,
    ) -> bool:
        assert real_delete(candidate) is True
        return False

    monkeypatch.setattr(
        storage,
        "delete_transcription_directory",
        delete_then_report_already_absent,
    )

    result = await StorageCleanupService(
        storage,
        RecordingJobLoader(),
        GRACE_PERIOD,
    ).cleanup(now=NOW)

    assert directory.exists() is False
    assert_cleanup_result(
        result,
        inspected=1,
        kept=0,
        deleted=1,
        orphan_deleted=1,
    )


async def test_global_database_failure_aborts_before_any_deletion(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)
    directories = [
        create_audio_directory(
            storage,
            tmp_path,
            uuid4(),
            modified_at=OLD_TIMESTAMP,
        )
        for _ in range(2)
    ]
    loader = RecordingJobLoader(error=ConnectionError("database unavailable"))

    with pytest.raises(StorageCleanupAbortedError):
        await StorageCleanupService(
            storage,
            loader,
            GRACE_PERIOD,
        ).cleanup(now=NOW)

    assert all(directory.is_dir() for directory in directories)
    assert len(loader.calls) == 1
    assert set(loader.calls[0]) == {UUID(directory.name) for directory in directories}


async def test_global_storage_scan_failure_aborts_cleanup(tmp_path: Path):
    root = tmp_path / "storage"
    storage = FileSystemAudioStorage(root)
    root.rmdir()
    loader = RecordingJobLoader()

    with pytest.raises(StorageCleanupAbortedError):
        await StorageCleanupService(
            storage,
            loader,
            GRACE_PERIOD,
        ).cleanup(now=NOW)

    assert loader.calls == []


async def test_cleanup_loads_all_old_candidates_in_one_batch(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)
    queued_job_uuid = uuid4()
    completed_job_uuid = uuid4()
    orphan_job_uuid = uuid4()
    for job_uuid in (queued_job_uuid, completed_job_uuid, orphan_job_uuid):
        create_audio_directory(
            storage,
            tmp_path,
            job_uuid,
            modified_at=OLD_TIMESTAMP,
        )
    loader = RecordingJobLoader(
        [
            make_job(queued_job_uuid, JobStatus.QUEUED),
            make_job(
                completed_job_uuid,
                JobStatus.COMPLETED,
                completed_at=OLD_TIMESTAMP,
            ),
        ]
    )

    result = await StorageCleanupService(
        storage,
        loader,
        GRACE_PERIOD,
    ).cleanup(now=NOW)

    assert len(loader.calls) == 1
    assert set(loader.calls[0]) == {
        queued_job_uuid,
        completed_job_uuid,
        orphan_job_uuid,
    }
    assert (tmp_path / str(queued_job_uuid)).is_dir()
    assert (tmp_path / str(completed_job_uuid)).exists() is False
    assert (tmp_path / str(orphan_job_uuid)).exists() is False
    assert_cleanup_result(
        result,
        inspected=3,
        kept=1,
        deleted=2,
        orphan_deleted=1,
    )


async def test_cleanup_is_idempotent_after_an_orphan_was_deleted(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)
    directory = create_audio_directory(
        storage,
        tmp_path,
        uuid4(),
        modified_at=OLD_TIMESTAMP,
    )
    loader = RecordingJobLoader()
    service = StorageCleanupService(storage, loader, GRACE_PERIOD)

    first_result = await service.cleanup(now=NOW)
    second_result = await service.cleanup(now=NOW)

    assert directory.exists() is False
    assert len(loader.calls) == 1
    assert_cleanup_result(
        first_result,
        inspected=1,
        kept=0,
        deleted=1,
        orphan_deleted=1,
    )
    assert_cleanup_result(
        second_result,
        inspected=0,
        kept=0,
        deleted=0,
    )
