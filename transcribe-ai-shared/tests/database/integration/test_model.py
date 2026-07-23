from sqlalchemy import inspect, select
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID

import pytest

from transcribe_ai_shared.database.models.job_model import Job, JobStatus, JobType


pytestmark = pytest.mark.integration


def test_postgresql_schema_contains_expected_native_types(setup_db):
    columns = {
        column["name"]: column
        for column in inspect(setup_db).get_columns(Job.__tablename__)
    }

    assert isinstance(columns["uuid"]["type"], UUID)
    assert isinstance(columns["type"]["type"], ENUM)
    assert isinstance(columns["status"]["type"], ENUM)
    assert isinstance(columns["result"]["type"], JSONB)
    assert columns["filename"]["nullable"] is False


def test_postgresql_applies_job_defaults_and_persists_mutable_json(db_session):
    job = Job(type=JobType.MONO_VOICE, filename="job.wav", result={})
    db_session.add(job)
    db_session.commit()

    assert job.id is not None
    assert job.uuid is not None
    assert job.status is JobStatus.PENDING
    assert job.created_at.tzinfo is not None

    job.result["text"] = "Bonjour"
    db_session.commit()
    db_session.expire_all()

    saved = db_session.scalar(select(Job).where(Job.id == job.id))
    assert saved.result == {"text": "Bonjour"}
