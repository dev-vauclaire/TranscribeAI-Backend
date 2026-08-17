from datetime import datetime, timezone
from uuid import uuid4

import pytest

from transcribe_ai_shared.database.base import NAMING_CONVENTION
from transcribe_ai_shared.database.models import (
    JobStatus,
    JobType,
    OutboxEvent,
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
    assert job.attempt_count == 1
    assert job.lease_owner == "worker-fast-1"


def test_outbox_event_can_be_constructed():
    event_uuid = uuid4()
    job_uuid = uuid4()
    now = datetime.now(timezone.utc)

    event = OutboxEvent(
        event_uuid=event_uuid,
        job_uuid=job_uuid,
        event_type="transcription.requested",
        created_at=now,
        locked_at=now,
        locked_by="dispatcher-1",
        attempt_count=2,
    )

    assert event.event_uuid == event_uuid
    assert event.job_uuid == job_uuid
    assert event.event_type == "transcription.requested"
    assert event.locked_by == "dispatcher-1"
    assert event.attempt_count == 2


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
    [TranscriptionJob, OutboxEvent, TranscriptionResult],
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
            OutboxEvent,
            {
                "event_uuid",
                "job_uuid",
                "event_type",
                "created_at",
                "published_at",
                "locked_at",
                "locked_by",
                "attempt_count",
                "last_error",
            },
            {"event_uuid"},
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


@pytest.mark.parametrize("model", [OutboxEvent, TranscriptionResult])
def test_child_models_reference_transcription_job(model):
    foreign_keys = {
        (foreign_key.parent.name, foreign_key.target_fullname, foreign_key.ondelete)
        for foreign_key in model.__table__.foreign_keys
    }

    assert foreign_keys == {("job_uuid", "transcription_jobs.job_uuid", "RESTRICT")}


def test_outbox_defines_unpublished_event_polling_index():
    index = next(
        index
        for index in OutboxEvent.__table__.indexes
        if index.name == "ix_outbox_events_unpublished_created_at"
    )

    assert [column.name for column in index.columns] == ["created_at"]
    assert str(index.dialect_options["postgresql"]["where"]) == ("published_at IS NULL")
