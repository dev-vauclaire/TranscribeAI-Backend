from datetime import datetime, timezone
from uuid import uuid4

import pytest

from transcribe_ai_shared.database.base import NAMING_CONVENTION
from transcribe_ai_shared.database.models import (
    JobStatus,
    JobType,
    TranscriptionJob,
    TranscriptionResult,
)


pytestmark = pytest.mark.unit


def test_job_enums_match_shared_contract():
    assert [status.value for status in JobStatus] == [
        "QUEUED",
        "PROCESSING",
        "COMPLETED",
        "FAILED",
    ]
    assert [job_type.value for job_type in JobType] == ["FAST", "BATCH"]


def test_transcription_job_can_be_constructed():
    job_uuid = uuid4()
    now = datetime.now(timezone.utc)

    job = TranscriptionJob(
        job_uuid=job_uuid,
        status=JobStatus.PROCESSING,
        job_type=JobType.FAST,
        audio_uri="audio/job.wav",
        dispatch_required=False,
        last_dispatched_at=now,
        created_at=now,
        updated_at=now,
        started_at=now,
        attempt_count=1,
        lease_owner="worker-fast-1",
        lease_expires_at=now,
    )

    assert job.job_uuid == job_uuid
    assert job.status is JobStatus.PROCESSING
    assert job.job_type is JobType.FAST
    assert job.audio_uri == "audio/job.wav"
    assert job.dispatch_required is False
    assert job.last_dispatched_at == now
    assert job.attempt_count == 1
    assert job.lease_owner == "worker-fast-1"


def test_transcription_job_dispatch_columns_match_contract():
    dispatch_required = TranscriptionJob.__table__.c.dispatch_required
    last_dispatched_at = TranscriptionJob.__table__.c.last_dispatched_at

    assert dispatch_required.nullable is False
    assert dispatch_required.default.arg is True
    assert str(dispatch_required.server_default.arg) == "true"
    assert last_dispatched_at.nullable is True
    assert last_dispatched_at.default is None
    assert last_dispatched_at.server_default is None


def test_transcription_job_indexes_match_repository_queries():
    indexes = {index.name: index for index in TranscriptionJob.__table__.indexes}

    assert indexes.keys() == {"idx_job_dispatch", "idx_job_expired_lease"}

    dispatch_index = indexes["idx_job_dispatch"]
    assert tuple(column.name for column in dispatch_index.columns) == ("created_at",)
    assert str(dispatch_index.dialect_options["postgresql"]["where"]) == (
        "status = 'QUEUED' AND dispatch_required IS TRUE"
    )

    expired_lease_index = indexes["idx_job_expired_lease"]
    assert tuple(column.name for column in expired_lease_index.columns) == (
        "lease_expires_at",
    )
    assert str(expired_lease_index.dialect_options["postgresql"]["where"]) == (
        "status = 'PROCESSING'"
    )


def test_transcription_result_can_be_constructed():
    job_uuid = uuid4()
    now = datetime.now(timezone.utc)
    payload = {"text": "Bonjour", "segments": [{"start": 0.0, "end": 1.0}]}

    result = TranscriptionResult(
        job_uuid=job_uuid,
        result=payload,
        speaker_count=1,
        model_name="whisper",
        model_version="large-v3",
        note="Transcription relue",
        created_at=now,
    )

    assert result.job_uuid == job_uuid
    assert result.result == payload
    assert result.speaker_count == 1
    assert result.model_name == "whisper"
    assert result.model_version == "large-v3"
    assert result.note == "Transcription relue"


@pytest.mark.parametrize(
    "model",
    [TranscriptionJob, TranscriptionResult],
)
def test_models_use_shared_metadata_naming_convention(model):
    assert model.metadata.naming_convention == NAMING_CONVENTION


@pytest.mark.parametrize(
    ("model", "expected_columns", "primary_key"),
    [
        (
            TranscriptionJob,
            {
                "job_uuid",
                "status",
                "job_type",
                "audio_uri",
                "dispatch_required",
                "last_dispatched_at",
                "created_at",
                "updated_at",
                "started_at",
                "completed_at",
                "attempt_count",
                "last_error",
                "lease_owner",
                "lease_expires_at",
            },
            {"job_uuid"},
        ),
        (
            TranscriptionResult,
            {
                "job_uuid",
                "result",
                "speaker_count",
                "model_name",
                "model_version",
                "note",
                "created_at",
            },
            {"job_uuid"},
        ),
    ],
)
def test_model_table_schemas_match_contract(model, expected_columns, primary_key):
    table = model.__table__

    assert set(table.columns.keys()) == expected_columns
    assert {column.name for column in table.primary_key.columns} == primary_key


def test_transcription_result_note_is_nullable():
    assert TranscriptionResult.__table__.c.note.nullable is True


def test_transcription_result_references_transcription_job():
    foreign_keys = {
        (foreign_key.parent.name, foreign_key.target_fullname, foreign_key.ondelete)
        for foreign_key in TranscriptionResult.__table__.foreign_keys
    }

    assert foreign_keys == {("job_uuid", "transcription_jobs.job_uuid", "RESTRICT")}
