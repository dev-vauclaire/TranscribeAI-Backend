from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from transcribe_ai_shared import (
    JobStatus,
    JobType,
    TranscriptionJob,
    TranscriptionJobRepository,
    transaction,
)


pytestmark = pytest.mark.integration


def make_job(**overrides) -> TranscriptionJob:
    values = {
        "job_type": JobType.FAST,
        "audio_uri": "audio/job.wav",
    }
    values.update(overrides)
    return TranscriptionJob(**values)


def test_add_and_find_job_by_uuid(session_factory):
    with transaction(session_factory) as session:
        repository = TranscriptionJobRepository(session)
        job = repository.add(make_job())
        job_uuid = job.job_uuid

    with transaction(session_factory) as session:
        saved = TranscriptionJobRepository(session).get_by_uuid(job_uuid)

    assert job_uuid is not None
    assert saved is not None
    assert saved.job_uuid == job_uuid


def test_update_status_starts_job_and_increments_attempt(session_factory):
    with transaction(session_factory) as session:
        repository = TranscriptionJobRepository(session)
        job_uuid = repository.add(make_job()).job_uuid

    with transaction(session_factory) as session:
        updated = TranscriptionJobRepository(session).update_status(
            job_uuid,
            JobStatus.PROCESSING,
        )

    with transaction(session_factory) as session:
        saved = TranscriptionJobRepository(session).get_by_uuid(job_uuid)

    assert updated is not None
    assert saved is not None
    assert saved.status is JobStatus.PROCESSING
    assert saved.started_at is not None
    assert saved.attempt_count == 1


def test_processing_retry_increments_attempt_and_preserves_start_time(
    session_factory,
):
    with transaction(session_factory) as session:
        repository = TranscriptionJobRepository(session)
        job_uuid = repository.add(make_job()).job_uuid
        first_attempt = repository.update_status(job_uuid, JobStatus.PROCESSING)
        assert first_attempt is not None
        first_started_at = first_attempt.started_at
        repository.update_status(job_uuid, JobStatus.QUEUED)
        repository.update_status(job_uuid, JobStatus.PROCESSING)

    with transaction(session_factory) as session:
        saved = TranscriptionJobRepository(session).get_by_uuid(job_uuid)

    assert saved is not None
    assert saved.attempt_count == 2
    assert saved.started_at == first_started_at


def test_complete_job_persists_result_and_completion_time(session_factory):
    with transaction(session_factory) as session:
        repository = TranscriptionJobRepository(session)
        job_uuid = repository.add(make_job(status=JobStatus.PROCESSING)).job_uuid

    with transaction(session_factory) as session:
        completed = TranscriptionJobRepository(session).complete_job(
            job_uuid,
            {"text": "Bonjour"},
            speaker_count=1,
            model_name="whisper",
            model_version="large-v3",
        )

    with transaction(session_factory) as session:
        saved = TranscriptionJobRepository(session).get_by_uuid(job_uuid)
        assert saved is not None
        result = saved.transcription_result

    assert completed is not None
    assert saved.status is JobStatus.COMPLETED
    assert saved.completed_at is not None
    assert saved.completed_at.utcoffset() == timezone.utc.utcoffset(saved.completed_at)
    assert result is not None
    assert result.result == {"text": "Bonjour"}
    assert result.speaker_count == 1
    assert result.model_name == "whisper"
    assert result.model_version == "large-v3"


def test_complete_job_updates_existing_result_idempotently(session_factory):
    first_completed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    later_completed_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    with transaction(session_factory) as session:
        repository = TranscriptionJobRepository(session)
        job_uuid = repository.add(make_job(status=JobStatus.PROCESSING)).job_uuid
        repository.complete_job(
            job_uuid,
            {"text": "First result"},
            model_name="whisper",
            model_version="large-v2",
            completed_at=first_completed_at,
        )

    with transaction(session_factory) as session:
        TranscriptionJobRepository(session).complete_job(
            job_uuid,
            {"text": "Final result"},
            model_name="whisper",
            model_version="large-v3",
            completed_at=later_completed_at,
        )

    with transaction(session_factory) as session:
        saved = TranscriptionJobRepository(session).get_by_uuid(job_uuid)
        assert saved is not None
        result = saved.transcription_result

    assert result is not None
    assert result.result == {"text": "Final result"}
    assert result.model_version == "large-v3"
    assert saved.completed_at == first_completed_at


def test_fail_job_records_error_and_completion_time(session_factory):
    with transaction(session_factory) as session:
        repository = TranscriptionJobRepository(session)
        job_uuid = repository.add(make_job(status=JobStatus.PROCESSING)).job_uuid

    completed_at = datetime.now(timezone.utc)
    with transaction(session_factory) as session:
        failed = TranscriptionJobRepository(session).fail_job(
            job_uuid,
            "Model unavailable",
            completed_at,
        )

    with transaction(session_factory) as session:
        saved = TranscriptionJobRepository(session).get_by_uuid(job_uuid)

    assert failed is not None
    assert saved is not None
    assert saved.status is JobStatus.FAILED
    assert saved.last_error == "Model unavailable"
    assert saved.completed_at == completed_at


def test_list_by_status_uses_stable_uuid_pagination(session_factory):
    created_at = datetime.now(timezone.utc)
    first_uuid = UUID(int=1)
    second_uuid = UUID(int=2)

    with transaction(session_factory) as session:
        repository = TranscriptionJobRepository(session)
        repository.add(make_job(job_uuid=first_uuid, created_at=created_at))
        repository.add(make_job(job_uuid=second_uuid, created_at=created_at))
        repository.add(make_job(status=JobStatus.FAILED))

    with transaction(session_factory) as session:
        jobs = TranscriptionJobRepository(session).list_by_status(
            JobStatus.QUEUED,
            limit=1,
            offset=1,
        )

    assert [job.job_uuid for job in jobs] == [second_uuid]


def test_repository_returns_none_for_unknown_job(session_factory):
    unknown_uuid = uuid4()

    with transaction(session_factory) as session:
        repository = TranscriptionJobRepository(session)
        assert repository.get_by_uuid(unknown_uuid) is None
        assert repository.update_status(unknown_uuid, JobStatus.PROCESSING) is None
        assert repository.complete_job(unknown_uuid, {}) is None
        assert repository.fail_job(unknown_uuid) is None
