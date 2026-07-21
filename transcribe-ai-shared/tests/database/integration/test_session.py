from uuid import uuid4

import pytest

from transcribe_ai_shared.database.models.job_model import Job, JobStatus, JobType
from transcribe_ai_shared.database.repositories.job_repository import JobRepository
from transcribe_ai_shared.database.session import create_session_factory, transaction


pytestmark = pytest.mark.integration


def test_transaction_commits_on_success(db_engine):
    factory = create_session_factory(db_engine)
    job_uuid = uuid4()

    with transaction(factory) as session:
        JobRepository(session).add(
            Job(
                uuid=job_uuid,
                type=JobType.MONO_VOICE,
                file_path="/audio/committed.wav",
            )
        )

    with factory() as session:
        assert JobRepository(session).get_by_uuid(job_uuid) is not None


def test_transaction_rolls_back_on_error(db_engine):
    factory = create_session_factory(db_engine)
    job_uuid = uuid4()

    with pytest.raises(RuntimeError, match="processing failed"):
        with transaction(factory) as session:
            JobRepository(session).add(
                Job(
                    uuid=job_uuid,
                    type=JobType.MONO_VOICE,
                    file_path="/audio/rolled-back.wav",
                )
            )
            raise RuntimeError("processing failed")

    with factory() as session:
        assert JobRepository(session).get_by_uuid(job_uuid) is None


def test_multiple_repository_operations_are_atomic(db_engine):
    factory = create_session_factory(db_engine)
    job_uuid = uuid4()

    with transaction(factory) as session:
        repository = JobRepository(session)
        job = repository.add(
            Job(
                uuid=job_uuid,
                type=JobType.MONO_VOICE,
                file_path="/audio/job.wav",
            )
        )
        repository.update_status(job.id, JobStatus.PROCESSING)
        repository.complete_job(job.id, {"text": "Bonjour"})

    with factory() as session:
        saved = JobRepository(session).get_by_uuid(job_uuid)
        assert saved.status is JobStatus.COMPLETED
        assert saved.result == {"text": "Bonjour"}


def test_repository_changes_are_rolled_back_together(db_engine):
    factory = create_session_factory(db_engine)
    job_uuid = uuid4()

    with transaction(factory) as session:
        JobRepository(session).add(
            Job(
                uuid=job_uuid,
                type=JobType.MONO_VOICE,
                file_path="/audio/job.wav",
            )
        )

    with pytest.raises(RuntimeError, match="processing failed"):
        with transaction(factory) as session:
            repository = JobRepository(session)
            job = repository.get_by_uuid(job_uuid)
            repository.update_status(job.id, JobStatus.PROCESSING)
            repository.complete_job(job.id, {"text": "Temporary result"})
            raise RuntimeError("processing failed")

    with factory() as session:
        saved = JobRepository(session).get_by_uuid(job_uuid)
        assert saved.status is JobStatus.PENDING
        assert saved.result is None
