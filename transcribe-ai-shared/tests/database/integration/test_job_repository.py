from datetime import datetime, timezone
from uuid import uuid4

import pytest

from transcribe_ai_shared import transaction, Job, JobStatus, JobType, JobRepository


pytestmark = pytest.mark.integration


def make_job(*, status=JobStatus.PENDING, filename="job.wav"):
    return Job(type=JobType.MONO_VOICE, status=status, filename=filename)


def test_add_and_find_job_by_database_id_and_public_uuid(session_factory):
    with transaction(session_factory) as session:
        repository = JobRepository(session)
        job = repository.add(make_job())
        job_id = job.id
        job_uuid = job.uuid

    with transaction(session_factory) as session:
        repository = JobRepository(session)
        searched_job_by_id = repository.get_by_id(job_id)
        searched_job_by_uuid = repository.get_by_uuid(job_uuid)

    assert job_id is not None
    assert job_uuid is not None
    assert searched_job_by_id is not None
    assert searched_job_by_uuid is not None
    assert searched_job_by_id.id == job_id
    assert searched_job_by_uuid.uuid == job_uuid


def test_update_status_persists_change(session_factory):
    with transaction(session_factory) as session:
        repository = JobRepository(session)
        job = repository.add(make_job())
        job_id = job.id

    with transaction(session_factory) as session:
        repository = JobRepository(session)
        updated = repository.update_status(job_id, JobStatus.PROCESSING)
        assert updated is not None
        updated_job_id = updated.id

    with transaction(session_factory) as session:
        repository = JobRepository(session)
        saved = repository.get_by_id(job_id)

    assert saved is not None
    assert updated_job_id == job_id
    assert saved.status is JobStatus.PROCESSING


def test_complete_job_records_result_and_utc_end_time(session_factory):
    with transaction(session_factory) as session:
        repository = JobRepository(session)
        job = repository.add(make_job(status=JobStatus.PROCESSING))
        job_id = job.id

    with transaction(session_factory) as session:
        repository = JobRepository(session)
        completed = repository.complete_job(job_id, {"text": "Bonjour"})
        assert completed is not None
        completed_job_id = completed.id

    with transaction(session_factory) as session:
        repository = JobRepository(session)
        saved = repository.get_by_id(job_id)

    assert saved is not None
    assert completed_job_id == job_id
    assert saved.status is JobStatus.COMPLETED
    assert saved.result == {"text": "Bonjour"}
    assert saved.ended_at is not None
    assert saved.ended_at.utcoffset() == timezone.utc.utcoffset(saved.ended_at)


def test_fail_job_records_error_and_provided_end_time(session_factory):
    with transaction(session_factory) as session:
        repository = JobRepository(session)
        job = repository.add(make_job(status=JobStatus.PROCESSING))
        job_id = job.id

    ended_at = datetime.now(timezone.utc)

    with transaction(session_factory) as session:
        repository = JobRepository(session)
        failed = repository.fail_job(job_id, "Whisper unavailable", ended_at)
        assert failed is not None
        failed_job_id = failed.id

    with transaction(session_factory) as session:
        repository = JobRepository(session)
        saved = repository.get_by_id(job_id)

    assert saved is not None
    assert failed_job_id == job_id
    assert saved.status is JobStatus.FAILED
    assert saved.error_message == "Whisper unavailable"
    assert saved.ended_at == ended_at


def test_list_by_status_filters_and_uses_stable_pagination(session_factory):
    created_at = datetime.now(timezone.utc)
    first = make_job()
    first.created_at = created_at
    second = make_job()
    second.created_at = created_at

    with transaction(session_factory) as session:
        repository = JobRepository(session)
        repository.add(first)
        repository.add(second)
        repository.add(make_job(status=JobStatus.FAILED))
        second_job_id = second.id

    with transaction(session_factory) as session:
        repository = JobRepository(session)
        jobs = repository.list_by_status(JobStatus.PENDING, limit=1, offset=1)
        returned_job_ids = [job.id for job in jobs]

    assert returned_job_ids == [second_job_id]


@pytest.mark.parametrize("status", [JobStatus.COMPLETED, JobStatus.FAILED])
def test_delete_job_removes_terminal_job(session_factory, status):
    with transaction(session_factory) as session:
        repository = JobRepository(session)
        job = repository.add(make_job(status=status))
        job_id = job.id

    with transaction(session_factory) as session:
        repository = JobRepository(session)
        is_deleted = repository.delete_job(job_id)

    with transaction(session_factory) as session:
        repository = JobRepository(session)
        saved = repository.get_by_id(job_id)

    assert is_deleted is True
    assert saved is None


@pytest.mark.parametrize("status", [JobStatus.PENDING, JobStatus.PROCESSING])
def test_delete_job_rejects_active_job(session_factory, status):
    with transaction(session_factory) as session:
        repository = JobRepository(session)
        job = repository.add(make_job(status=status))
        job_id = job.id

    with transaction(session_factory) as session:
        repository = JobRepository(session)
        is_deleted = repository.delete_job(job_id)

    with transaction(session_factory) as session:
        repository = JobRepository(session)
        saved = repository.get_by_id(job_id)

    assert is_deleted is False
    assert saved is not None
    assert saved.id == job_id


def test_repository_returns_neutral_values_for_unknown_job(session_factory):
    with transaction(session_factory) as session:
        repository = JobRepository(session)
        unknown_id = 999_999
        assert repository.get_by_id(unknown_id) is None
        assert repository.get_by_uuid(uuid4()) is None
        assert repository.update_status(unknown_id, JobStatus.PROCESSING) is None
        assert repository.complete_job(unknown_id, {}) is None
        assert repository.fail_job(unknown_id) is None
        assert repository.delete_job(unknown_id) is False
