from datetime import datetime, timezone
from uuid import uuid4

import pytest

from transcribe_ai_shared.database.models.job_model import Job, JobStatus, JobType
from transcribe_ai_shared.database.repositories.job_repository import JobRepository


pytestmark = pytest.mark.integration


def make_job(*, status=JobStatus.PENDING, filename="job.wav"):
    return Job(type=JobType.MONO_VOICE, status=status, filename=filename)


def test_add_and_find_job_by_database_id_and_public_uuid(db_session):
    repository = JobRepository(db_session)
    job = repository.add(make_job())

    assert job.id is not None
    assert job.uuid is not None
    assert repository.get_by_id(job.id) is job
    assert repository.get_by_uuid(job.uuid) is job


def test_update_status_persists_change(db_session):
    repository = JobRepository(db_session)
    job = repository.add(make_job())

    updated = repository.update_status(job.id, JobStatus.PROCESSING)
    db_session.expire_all()
    saved = repository.get_by_id(job.id)

    assert updated is job
    assert saved.status is JobStatus.PROCESSING


def test_complete_job_records_result_and_utc_end_time(db_session):
    repository = JobRepository(db_session)
    job = repository.add(make_job(status=JobStatus.PROCESSING))

    completed = repository.complete_job(job.id, {"text": "Bonjour"})
    db_session.expire_all()
    saved = repository.get_by_id(job.id)

    assert completed is job
    assert saved.status is JobStatus.COMPLETED
    assert saved.result == {"text": "Bonjour"}
    assert saved.ended_at.utcoffset() == timezone.utc.utcoffset(saved.ended_at)


def test_fail_job_records_error_and_provided_end_time(db_session):
    repository = JobRepository(db_session)
    job = repository.add(make_job(status=JobStatus.PROCESSING))
    ended_at = datetime.now(timezone.utc)

    failed = repository.fail_job(job.id, "Whisper unavailable", ended_at)
    db_session.expire_all()
    saved = repository.get_by_id(job.id)

    assert failed is job
    assert saved.status is JobStatus.FAILED
    assert saved.error_message == "Whisper unavailable"
    assert saved.ended_at == ended_at


def test_list_by_status_filters_and_uses_stable_pagination(db_session):
    repository = JobRepository(db_session)
    created_at = datetime.now(timezone.utc)
    first = make_job()
    first.created_at = created_at
    second = make_job()
    second.created_at = created_at
    repository.add(first)
    repository.add(second)
    repository.add(make_job(status=JobStatus.FAILED))
    db_session.commit()

    jobs = repository.list_by_status(JobStatus.PENDING, limit=1, offset=1)

    assert jobs == [second]


@pytest.mark.parametrize("status", [JobStatus.COMPLETED, JobStatus.FAILED])
def test_delete_job_removes_terminal_job(db_session, status):
    repository = JobRepository(db_session)
    job = repository.add(make_job(status=status))
    job_id = job.id

    assert repository.delete_job(job_id) is True
    assert repository.get_by_id(job_id) is None


@pytest.mark.parametrize("status", [JobStatus.PENDING, JobStatus.PROCESSING])
def test_delete_job_rejects_active_job(db_session, status):
    repository = JobRepository(db_session)
    job = repository.add(make_job(status=status))

    assert repository.delete_job(job.id) is False
    assert repository.get_by_id(job.id) is job


def test_repository_returns_neutral_values_for_unknown_job(db_session):
    repository = JobRepository(db_session)
    unknown_id = 999_999

    assert repository.get_by_id(unknown_id) is None
    assert repository.get_by_uuid(uuid4()) is None
    assert repository.update_status(unknown_id, JobStatus.PROCESSING) is None
    assert repository.complete_job(unknown_id, {}) is None
    assert repository.fail_job(unknown_id) is None
    assert repository.delete_job(unknown_id) is False
