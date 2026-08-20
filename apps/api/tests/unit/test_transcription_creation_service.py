from contextlib import nullcontext
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from types import TracebackType
from typing import Self
from unittest.mock import AsyncMock, MagicMock, call, create_autospec

import pytest
from sqlalchemy.exc import SQLAlchemyError

import api.Services.create_transcription as create_transcription_module
from api.Media.models import AudioFormat, AudioMetadata
from api.Media.protocols import MediaProbe
from api.Services.create_transcription import CreateTranscriptionService
from api.exceptions import (
    AudioStorageUnavailableError,
    AudioTooLongError,
    InvalidAudioFileError,
    MediaProbeUnavailableError,
    TranscriptionPersistenceError,
    UnsupportedAudioCodecError,
    UnsupportedAudioFormatError,
)
from transcribe_ai_shared import (
    AudioLocation,
    AudioStorage,
    JobRepository,
    JobStatus,
    JobType,
    TranscriptionJob,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

AUDIO_CONTENT = b"valid audio bytes"
FAST_LIMIT = Decimal("60")
BATCH_LIMIT = Decimal("3600")


@pytest.fixture(autouse=True)
def execute_blocking_calls_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isole le use case de l'ordonnanceur de threads."""

    async def run_inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(create_transcription_module.asyncio, "to_thread", run_inline)


class RecordingTransaction:
    """Double qui rend visibles le commit, le rollback et leur erreur."""

    def __init__(self, *, commit_error: BaseException | None = None) -> None:
        self.session = object()
        self.commit_error = commit_error
        self.entered = False
        self.committed = False
        self.rolled_back = False
        self.exit_exception_type: type[BaseException] | None = None

    async def __aenter__(self) -> object:
        self.entered = True
        return self.session

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exit_exception_type = exception_type
        if exception_type is not None:
            self.rolled_back = True
            return
        if self.commit_error is not None:
            raise self.commit_error
        self.committed = True


class RecordingSessionFactory:
    """Expose transaction et session de vérification post-COMMIT."""

    def __init__(self, transaction: RecordingTransaction) -> None:
        self.transaction = transaction
        self.begin_call_count = 0
        self.verification_call_count = 0

    def begin(self) -> RecordingTransaction:
        self.begin_call_count += 1
        return self.transaction

    def __call__(self) -> Self:
        self.verification_call_count += 1
        return self

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


@dataclass
class ServiceHarness:
    service: CreateTranscriptionService
    storage: MagicMock
    media_probe: MagicMock
    repository: MagicMock
    repository_factory: MagicMock
    transaction: RecordingTransaction
    session_factory: RecordingSessionFactory
    stored_location: AudioLocation | None = None


def build_service_harness(
    *,
    metadata: AudioMetadata | None = None,
    commit_error: BaseException | None = None,
    persisted_after_commit_error: bool = False,
    verification_error: BaseException | None = None,
) -> ServiceHarness:
    storage = create_autospec(AudioStorage, instance=True)
    media_probe = create_autospec(MediaProbe, instance=True)
    media_probe.probe.return_value = metadata or AudioMetadata(
        duration_seconds=Decimal("30"),
        format=AudioFormat.WAV,
        codec="pcm_s16le",
    )
    repository = create_autospec(JobRepository, instance=True)
    repository.add = AsyncMock()
    repository.get_by_uuid = AsyncMock(
        return_value=object() if persisted_after_commit_error else None,
        side_effect=verification_error,
    )
    repository_factory = MagicMock(return_value=repository)
    transaction = RecordingTransaction(commit_error=commit_error)
    session_factory = RecordingSessionFactory(transaction)
    harness = ServiceHarness(
        service=CreateTranscriptionService(
            storage=storage,
            media_probe=media_probe,
            session_factory=session_factory,  # type: ignore[arg-type]
            fast_max_duration_seconds=FAST_LIMIT,
            batch_max_duration_seconds=BATCH_LIMIT,
            repository_factory=repository_factory,
        ),
        storage=storage,
        media_probe=media_probe,
        repository=repository,
        repository_factory=repository_factory,
        transaction=transaction,
        session_factory=session_factory,
    )

    def save(job_uuid, source, *, extension):
        assert source is not None
        location = AudioLocation(uri=f"{job_uuid}/input.{extension}")
        harness.stored_location = location
        return location

    storage.save.side_effect = save
    storage.open.return_value = nullcontext(BytesIO(AUDIO_CONTENT))
    return harness


def persisted_job(harness: ServiceHarness) -> TranscriptionJob:
    return harness.repository.add.await_args.args[0]


@pytest.mark.parametrize(
    ("job_type", "duration"),
    [(JobType.FAST, FAST_LIMIT), (JobType.BATCH, BATCH_LIMIT)],
)
async def test_create_persists_a_queued_job_for_fast_and_batch(
    job_type: JobType,
    duration: Decimal,
) -> None:
    harness = build_service_harness(
        metadata=AudioMetadata(
            duration_seconds=duration,
            format=AudioFormat.WAV,
            codec="pcm_s16le",
        )
    )
    source = BytesIO(AUDIO_CONTENT)

    result = await harness.service.create(
        source=source,
        extension="wav",
        job_type=job_type,
    )

    job = persisted_job(harness)
    assert result.job_uuid == job.job_uuid == harness.stored_location.job_uuid
    assert result.status is JobStatus.QUEUED
    assert job.status is JobStatus.QUEUED
    assert job.job_type is job_type
    assert job.audio_uri == harness.stored_location.uri
    assert job.dispatch_required is True
    harness.storage.save.assert_called_once_with(job.job_uuid, source, extension="wav")
    harness.storage.open.assert_called_once_with(harness.stored_location)
    probe_source = harness.media_probe.probe.call_args.args[0]
    assert probe_source.getvalue() == AUDIO_CONTENT
    assert harness.transaction.committed is True
    assert harness.transaction.rolled_back is False
    harness.storage.delete.assert_not_called()


@pytest.mark.parametrize(
    ("job_type", "limit"),
    [(JobType.FAST, FAST_LIMIT), (JobType.BATCH, BATCH_LIMIT)],
)
async def test_create_rejects_audio_above_the_job_type_duration_limit(
    job_type: JobType,
    limit: Decimal,
) -> None:
    harness = build_service_harness(
        metadata=AudioMetadata(
            duration_seconds=limit + Decimal("0.001"),
            format=AudioFormat.WAV,
            codec="pcm_s16le",
        )
    )

    with pytest.raises(AudioTooLongError):
        await harness.service.create(
            source=BytesIO(AUDIO_CONTENT),
            extension="wav",
            job_type=job_type,
        )

    harness.storage.delete.assert_called_once_with(harness.stored_location)
    harness.repository.add.assert_not_awaited()
    assert harness.transaction.entered is False


async def test_create_rejects_real_container_that_differs_from_extension() -> None:
    harness = build_service_harness(
        metadata=AudioMetadata(
            duration_seconds=Decimal("10"),
            format=AudioFormat.MP3,
            codec="mp3",
        )
    )

    with pytest.raises(UnsupportedAudioFormatError):
        await harness.service.create(
            source=BytesIO(AUDIO_CONTENT),
            extension="wav",
            job_type=JobType.FAST,
        )

    harness.storage.delete.assert_called_once_with(harness.stored_location)
    harness.repository.add.assert_not_awaited()


@pytest.mark.parametrize(
    "probe_error",
    [
        InvalidAudioFileError("invalid audio"),
        UnsupportedAudioCodecError("unsupported codec"),
        MediaProbeUnavailableError("probe unavailable"),
    ],
)
async def test_create_cleans_audio_when_media_probe_rejects_it(
    probe_error: Exception,
) -> None:
    harness = build_service_harness()
    harness.media_probe.probe.side_effect = probe_error

    with pytest.raises(type(probe_error)) as captured:
        await harness.service.create(
            source=BytesIO(AUDIO_CONTENT),
            extension="wav",
            job_type=JobType.FAST,
        )

    assert captured.value is probe_error
    harness.storage.delete.assert_called_once_with(harness.stored_location)
    harness.repository.add.assert_not_awaited()


@pytest.mark.parametrize("duration", [Decimal("NaN"), Decimal("0"), Decimal("-1")])
async def test_create_rejects_invalid_metadata_from_an_alternate_probe(
    duration: Decimal,
) -> None:
    harness = build_service_harness(
        metadata=AudioMetadata(
            duration_seconds=duration,
            format=AudioFormat.WAV,
            codec="pcm_s16le",
        )
    )

    with pytest.raises(InvalidAudioFileError):
        await harness.service.create(
            source=BytesIO(AUDIO_CONTENT),
            extension="wav",
            job_type=JobType.FAST,
        )

    harness.storage.delete.assert_called_once_with(harness.stored_location)
    harness.repository.add.assert_not_awaited()


async def test_cleanup_failure_does_not_hide_original_media_error() -> None:
    harness = build_service_harness()
    original_error = InvalidAudioFileError("invalid audio")
    harness.media_probe.probe.side_effect = original_error
    harness.storage.delete.side_effect = OSError("volume unavailable")

    with pytest.raises(InvalidAudioFileError) as captured:
        await harness.service.create(
            source=BytesIO(AUDIO_CONTENT),
            extension="wav",
            job_type=JobType.FAST,
        )

    assert captured.value is original_error
    harness.storage.delete.assert_called_once_with(harness.stored_location)


async def test_create_translates_storage_failure_without_probe_or_transaction() -> None:
    harness = build_service_harness()
    harness.storage.save.side_effect = OSError("volume unavailable")

    with pytest.raises(AudioStorageUnavailableError):
        await harness.service.create(
            source=BytesIO(AUDIO_CONTENT),
            extension="wav",
            job_type=JobType.FAST,
        )

    harness.media_probe.probe.assert_not_called()
    harness.storage.delete.assert_not_called()
    harness.repository.add.assert_not_awaited()
    assert harness.transaction.entered is False


async def test_create_cleans_audio_when_storage_cannot_reopen_it() -> None:
    harness = build_service_harness()
    harness.storage.open.side_effect = OSError("volume unavailable")

    with pytest.raises(AudioStorageUnavailableError):
        await harness.service.create(
            source=BytesIO(AUDIO_CONTENT),
            extension="wav",
            job_type=JobType.FAST,
        )

    harness.storage.delete.assert_called_once_with(harness.stored_location)
    harness.media_probe.probe.assert_not_called()


@pytest.mark.parametrize(
    "database_error",
    [SQLAlchemyError("database unavailable"), RuntimeError("adapter failed")],
)
async def test_create_rolls_back_and_cleans_audio_when_repository_add_fails(
    database_error: Exception,
) -> None:
    harness = build_service_harness()
    harness.repository.add.side_effect = database_error

    with pytest.raises(TranscriptionPersistenceError):
        await harness.service.create(
            source=BytesIO(AUDIO_CONTENT),
            extension="wav",
            job_type=JobType.FAST,
        )

    harness.storage.delete.assert_called_once_with(harness.stored_location)
    assert harness.transaction.rolled_back is True
    assert harness.transaction.exit_exception_type is type(database_error)


async def test_create_preserves_audio_when_commit_cannot_be_confirmed() -> None:
    harness = build_service_harness(
        commit_error=SQLAlchemyError("commit acknowledgement unavailable")
    )

    with pytest.raises(TranscriptionPersistenceError):
        await harness.service.create(
            source=BytesIO(AUDIO_CONTENT),
            extension="wav",
            job_type=JobType.FAST,
        )

    harness.repository.add.assert_awaited_once()
    harness.repository.get_by_uuid.assert_awaited_once_with(
        harness.stored_location.job_uuid
    )
    assert harness.repository_factory.call_args_list == [
        call(harness.transaction.session),
        call(harness.session_factory),
    ]
    harness.storage.delete.assert_not_called()


async def test_create_returns_success_when_commit_was_persisted_despite_error() -> None:
    harness = build_service_harness(
        commit_error=SQLAlchemyError("commit acknowledgement unavailable"),
        persisted_after_commit_error=True,
    )

    result = await harness.service.create(
        source=BytesIO(AUDIO_CONTENT),
        extension="wav",
        job_type=JobType.FAST,
    )

    assert result.job_uuid == harness.stored_location.job_uuid
    assert result.status is JobStatus.QUEUED
    harness.storage.delete.assert_not_called()


async def test_create_preserves_audio_when_commit_verification_is_unavailable() -> None:
    harness = build_service_harness(
        commit_error=SQLAlchemyError("commit acknowledgement unavailable"),
        verification_error=SQLAlchemyError("database still unavailable"),
    )

    with pytest.raises(TranscriptionPersistenceError):
        await harness.service.create(
            source=BytesIO(AUDIO_CONTENT),
            extension="wav",
            job_type=JobType.FAST,
        )

    harness.storage.delete.assert_not_called()
    assert harness.session_factory.verification_call_count == 1


@pytest.mark.parametrize(
    ("fast_limit", "batch_limit"),
    [
        (Decimal("NaN"), Decimal("60")),
        (Decimal("1"), Decimal("Infinity")),
        (Decimal("0"), Decimal("60")),
        (Decimal("61"), Decimal("60")),
    ],
)
async def test_service_rejects_invalid_duration_configuration(
    fast_limit: Decimal,
    batch_limit: Decimal,
) -> None:
    storage = create_autospec(AudioStorage, instance=True)
    media_probe = create_autospec(MediaProbe, instance=True)

    with pytest.raises(ValueError):
        CreateTranscriptionService(
            storage=storage,
            media_probe=media_probe,
            session_factory=MagicMock(),  # type: ignore[arg-type]
            fast_max_duration_seconds=fast_limit,
            batch_max_duration_seconds=batch_limit,
        )
