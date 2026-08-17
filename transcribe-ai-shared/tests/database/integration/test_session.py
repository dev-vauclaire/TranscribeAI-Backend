from uuid import uuid4

import pytest

from transcribe_ai_shared.database import (
    JobStatus,
    JobType,
    TranscriptionJob,
    TranscriptionJobRepository,
    check_postgres_connection,
    transaction,
)


pytestmark = pytest.mark.integration


def make_job(job_uuid):
    return TranscriptionJob(
        job_uuid=job_uuid,
        job_type=JobType.FAST,
        audio_uri="audio/job.wav",
    )


def test_check_postgres_connection_executes_probe(session_factory):
    check_postgres_connection(session_factory)


def test_transaction_commits_on_success(session_factory):
    job_uuid = uuid4()

    with transaction(session_factory) as session:
        TranscriptionJobRepository(session).add(make_job(job_uuid))

    with session_factory() as session:
        job = TranscriptionJobRepository(session).get_by_uuid(job_uuid)
        assert job is not None
        assert job.audio_uri == "audio/job.wav"


def test_transaction_rolls_back_on_error(session_factory):
    job_uuid = uuid4()

    with pytest.raises(RuntimeError, match="processing failed"):
        with transaction(session_factory) as session:
            TranscriptionJobRepository(session).add(make_job(job_uuid))
            raise RuntimeError("processing failed")

    with session_factory() as session:
        assert TranscriptionJobRepository(session).get_by_uuid(job_uuid) is None


def test_multiple_repository_operations_are_atomic(session_factory):
    job_uuid = uuid4()

    with transaction(session_factory) as session:
        repository = TranscriptionJobRepository(session)
        repository.add(make_job(job_uuid))
        repository.update_status(job_uuid, JobStatus.PROCESSING)
        repository.complete_job(job_uuid, {"text": "Bonjour"})

    with session_factory() as session:
        saved = TranscriptionJobRepository(session).get_by_uuid(job_uuid)
        assert saved is not None
        assert saved.status is JobStatus.COMPLETED
        assert saved.transcription_result is not None
        assert saved.transcription_result.result == {"text": "Bonjour"}


def test_repository_changes_are_rolled_back_together(session_factory):
    job_uuid = uuid4()

    with transaction(session_factory) as session:
        TranscriptionJobRepository(session).add(make_job(job_uuid))

    with pytest.raises(RuntimeError, match="processing failed"):
        with transaction(session_factory) as session:
            repository = TranscriptionJobRepository(session)
            repository.update_status(job_uuid, JobStatus.PROCESSING)
            repository.complete_job(job_uuid, {"text": "Temporary result"})
            raise RuntimeError("processing failed")

    with session_factory() as session:
        saved = TranscriptionJobRepository(session).get_by_uuid(job_uuid)
        assert saved is not None
        assert saved.status is JobStatus.QUEUED
        assert saved.transcription_result is None
