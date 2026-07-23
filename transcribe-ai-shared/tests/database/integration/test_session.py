from uuid import uuid4

import pytest

from transcribe_ai_shared.database.models.job_model import Job, JobStatus, JobType
from transcribe_ai_shared.database.repositories.job_repository import JobRepository
from transcribe_ai_shared.database.session import (
    check_postgres_connection,
    transaction,
)


pytestmark = pytest.mark.integration


def test_check_postgres_connection_executes_probe(session_factory):
    check_postgres_connection(session_factory)


def test_transaction_commits_on_success(session_factory):
    job_uuid = uuid4()

    with transaction(session_factory) as session:
        JobRepository(session).add(
            Job(
                uuid=job_uuid,
                type=JobType.MONO_VOICE,
                filename="committed.wav",
            )
        )

    with session_factory() as session:
        job = JobRepository(session).get_by_uuid(job_uuid)
        assert job is not None
        assert job.filename == "committed.wav"


def test_transaction_rolls_back_on_error(session_factory):
    job_uuid = uuid4()

    with pytest.raises(RuntimeError, match="processing failed"):
        with transaction(session_factory) as session:
            JobRepository(session).add(
                Job(
                    uuid=job_uuid,
                    type=JobType.MONO_VOICE,
                    filename="rolled-back.wav",
                )
            )
            raise RuntimeError("processing failed")

    with session_factory() as session:
        assert JobRepository(session).get_by_uuid(job_uuid) is None


def test_multiple_repository_operations_are_atomic(session_factory):
    job_uuid = uuid4()

    with transaction(session_factory) as session:
        repository = JobRepository(session)
        job = repository.add(
            Job(
                uuid=job_uuid,
                type=JobType.MONO_VOICE,
                filename="job.wav",
            )
        )
        repository.update_status(job.id, JobStatus.PROCESSING)
        repository.complete_job(job.id, {"text": "Bonjour"})

    with session_factory() as session:
        saved = JobRepository(session).get_by_uuid(job_uuid)
        assert saved.status is JobStatus.COMPLETED
        assert saved.result == {"text": "Bonjour"}


def test_repository_changes_are_rolled_back_together(session_factory):
    job_uuid = uuid4()

    with transaction(session_factory) as session:
        repository = JobRepository(session)
        repository.add(
            Job(
                uuid=job_uuid,
                type=JobType.MONO_VOICE,
                filename="job.wav",
            )
        )

    with pytest.raises(RuntimeError, match="processing failed"):
        with transaction(session_factory) as session:
            repository = JobRepository(session)
            job = repository.get_by_uuid(job_uuid)
            repository.update_status(job.id, JobStatus.PROCESSING)
            repository.complete_job(job.id, {"text": "Temporary result"})
            raise RuntimeError("processing failed")

    with session_factory() as session:
        saved = JobRepository(session).get_by_uuid(job_uuid)
        assert saved.status is JobStatus.PENDING
        assert saved.result is None
