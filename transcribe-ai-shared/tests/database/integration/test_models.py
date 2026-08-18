from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import Boolean, DateTime, Text, delete, inspect, text
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.exc import DBAPIError, IntegrityError

from transcribe_ai_shared.database.models import (
    JobStatus,
    JobType,
    TranscriptionJob,
    TranscriptionResult,
)


pytestmark = pytest.mark.integration


def make_job(**overrides) -> TranscriptionJob:
    values = {
        "job_uuid": uuid4(),
        "job_type": JobType.FAST,
        "audio_uri": "audio/job.wav",
    }
    values.update(overrides)
    return TranscriptionJob(**values)


def test_postgresql_contains_migrated_transcription_tables(setup_db):
    table_names = set(inspect(setup_db).get_table_names())

    assert table_names == {
        "alembic_version",
        "transcription_jobs",
        "transcription_results",
    }


@pytest.mark.parametrize(
    ("model", "primary_key"),
    [
        (TranscriptionJob, ["job_uuid"]),
        (TranscriptionResult, ["job_uuid"]),
    ],
)
def test_postgresql_primary_keys_match_contract(setup_db, model, primary_key):
    constraint = inspect(setup_db).get_pk_constraint(model.__tablename__)

    assert constraint["constrained_columns"] == primary_key


def test_postgresql_uses_native_uuid_jsonb_and_enums(setup_db):
    inspector = inspect(setup_db)
    job_columns = {
        column["name"]: column
        for column in inspector.get_columns(TranscriptionJob.__tablename__)
    }
    result_columns = {
        column["name"]: column
        for column in inspector.get_columns(TranscriptionResult.__tablename__)
    }

    assert isinstance(job_columns["job_uuid"]["type"], UUID)
    assert isinstance(job_columns["status"]["type"], ENUM)
    assert job_columns["status"]["type"].enums == [
        "QUEUED",
        "PROCESSING",
        "COMPLETED",
        "FAILED",
    ]
    assert isinstance(job_columns["job_type"]["type"], ENUM)
    assert job_columns["job_type"]["type"].enums == ["FAST", "BATCH"]
    assert isinstance(job_columns["dispatch_required"]["type"], Boolean)
    assert job_columns["dispatch_required"]["nullable"] is False
    assert job_columns["dispatch_required"]["default"] == "true"
    assert isinstance(job_columns["last_dispatched_at"]["type"], DateTime)
    assert job_columns["last_dispatched_at"]["type"].timezone is True
    assert job_columns["last_dispatched_at"]["nullable"] is True
    assert job_columns["last_dispatched_at"]["default"] is None
    assert isinstance(result_columns["job_uuid"]["type"], UUID)
    assert isinstance(result_columns["result"]["type"], JSONB)
    assert isinstance(result_columns["note"]["type"], Text)
    assert result_columns["note"]["nullable"] is True


def test_postgresql_result_foreign_key_restricts_parent_changes(setup_db):
    foreign_keys = inspect(setup_db).get_foreign_keys(TranscriptionResult.__tablename__)

    assert len(foreign_keys) == 1
    foreign_key = foreign_keys[0]
    assert foreign_key["constrained_columns"] == ["job_uuid"]
    assert foreign_key["referred_table"] == "transcription_jobs"
    assert foreign_key["referred_columns"] == ["job_uuid"]
    assert foreign_key["options"] == {
        "ondelete": "RESTRICT",
        "onupdate": "RESTRICT",
    }


@pytest.mark.parametrize("job_type", [JobType.FAST, JobType.BATCH])
def test_job_defaults_and_enums_are_persisted(db_session, job_type):
    job = make_job(job_type=job_type)
    db_session.add(job)
    db_session.commit()
    db_session.expire_all()

    saved = db_session.get(TranscriptionJob, job.job_uuid)

    assert saved is not None
    assert saved.status is JobStatus.QUEUED
    assert saved.job_type is job_type
    assert saved.dispatch_required is True
    assert saved.last_dispatched_at is None
    assert saved.attempt_count == 0
    assert saved.created_at.tzinfo is not None
    assert saved.updated_at.tzinfo is not None


def test_job_dispatch_state_is_persisted(session_factory):
    dispatched_at = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)

    with session_factory.begin() as write_session:
        job = make_job(
            dispatch_required=False,
            last_dispatched_at=dispatched_at,
        )
        write_session.add(job)
        write_session.flush()
        job_uuid = job.job_uuid

    with session_factory() as read_session:
        saved = read_session.get(TranscriptionJob, job_uuid)

        assert saved is not None
        assert saved.dispatch_required is False
        assert saved.last_dispatched_at == dispatched_at
        assert saved.last_dispatched_at.tzinfo is not None


def test_result_defaults_and_relationship_are_persisted(session_factory):
    with session_factory.begin() as write_session:
        job = make_job()
        result = TranscriptionResult(
            job=job,
            result={"text": "Bonjour"},
        )
        write_session.add_all([job, result])
        write_session.flush()
        job_uuid = job.job_uuid

    with session_factory() as read_session:
        saved_job = read_session.get(TranscriptionJob, job_uuid)
        saved_result = read_session.get(TranscriptionResult, job_uuid)

        assert saved_job is not None
        assert saved_result is not None
        assert saved_result.created_at.tzinfo is not None
        assert saved_result.note is None
        assert saved_job.transcription_result is saved_result
        assert saved_result.job is saved_job


@pytest.mark.parametrize("note", [None, "Transcription relue"])
def test_transcription_result_note_is_persisted(session_factory, note):
    with session_factory.begin() as write_session:
        job = make_job()
        write_session.add_all(
            [
                job,
                TranscriptionResult(
                    job=job,
                    result={"text": "Bonjour"},
                    note=note,
                ),
            ]
        )
        write_session.flush()
        job_uuid = job.job_uuid

    with session_factory() as read_session:
        saved_result = read_session.get(TranscriptionResult, job_uuid)

        assert saved_result is not None
        assert saved_result.note == note


def test_duplicate_transcription_job_uuid_is_rejected(db_session):
    job_uuid = uuid4()
    db_session.add(make_job(job_uuid=job_uuid))
    db_session.commit()
    db_session.add(make_job(job_uuid=job_uuid, audio_uri="audio/duplicate.wav"))

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


@pytest.mark.parametrize(
    "overrides",
    [
        {"attempt_count": -1},
        {"audio_uri": "   "},
        {"lease_owner": "worker-fast-1"},
        {"lease_expires_at": datetime.now(timezone.utc)},
    ],
)
def test_transcription_job_check_constraints_are_enforced(db_session, overrides):
    db_session.add(make_job(**overrides))

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


def test_result_with_unknown_job_is_rejected(db_session):
    unknown_job_uuid = uuid4()
    db_session.add(
        TranscriptionResult(
            job_uuid=unknown_job_uuid,
            result={"text": "Bonjour"},
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


def test_transcription_result_is_unique_per_job(db_session):
    job = make_job()
    db_session.add(job)
    db_session.flush()
    db_session.add(TranscriptionResult(job_uuid=job.job_uuid, result={"text": "first"}))
    db_session.commit()
    db_session.expunge_all()
    db_session.add(
        TranscriptionResult(job_uuid=job.job_uuid, result={"text": "second"})
    )

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


@pytest.mark.parametrize(
    "overrides",
    [
        {"speaker_count": -1},
        {"model_name": ""},
        {"model_version": "   "},
    ],
)
def test_transcription_result_check_constraints_are_enforced(
    db_session,
    overrides,
):
    job = make_job()
    db_session.add(job)
    db_session.flush()
    values = {"job_uuid": job.job_uuid, "result": {"text": "Bonjour"}}
    values.update(overrides)
    db_session.add(TranscriptionResult(**values))

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


def test_jsonb_result_round_trip_and_mutation_are_persisted(db_session):
    job = make_job()
    payload = {
        "text": "Bonjour",
        "segments": [{"start": 0.0, "end": 1.0, "speaker": None}],
        "metadata": {"confidence": 0.98, "reviewed": False},
    }
    result = TranscriptionResult(job=job, result=payload)
    db_session.add_all([job, result])
    db_session.commit()

    result.result["language"] = "fr"
    db_session.commit()
    db_session.expire_all()

    saved = db_session.get(TranscriptionResult, job.job_uuid)
    assert saved is not None
    assert saved.result == {**payload, "language": "fr"}


@pytest.mark.parametrize(
    ("status", "job_type"),
    [("UNKNOWN", "FAST"), ("QUEUED", "UNKNOWN")],
)
def test_postgresql_rejects_unknown_enum_values(db_session, status, job_type):
    statement = text(
        "INSERT INTO transcription_jobs "
        "(job_uuid, status, job_type, audio_uri) VALUES "
        "(:job_uuid, CAST(:status AS transcription_job_status), "
        "CAST(:job_type AS transcription_job_type), :audio_uri)"
    )

    with pytest.raises(DBAPIError):
        db_session.execute(
            statement,
            {
                "job_uuid": uuid4(),
                "status": status,
                "job_type": job_type,
                "audio_uri": "audio/job.wav",
            },
        )
        db_session.commit()

    db_session.rollback()


def test_job_referenced_by_result_cannot_be_deleted(db_session):
    job = make_job()
    db_session.add(job)
    db_session.flush()
    child = TranscriptionResult(
        job_uuid=job.job_uuid,
        result={"text": "Bonjour"},
    )
    db_session.add(child)
    db_session.commit()
    job_uuid = job.job_uuid

    with pytest.raises(IntegrityError):
        db_session.execute(
            delete(TranscriptionJob).where(TranscriptionJob.job_uuid == job_uuid)
        )
        db_session.commit()

    db_session.rollback()
    assert db_session.get(TranscriptionJob, job_uuid) is not None
    assert db_session.get(TranscriptionResult, job_uuid) is not None
