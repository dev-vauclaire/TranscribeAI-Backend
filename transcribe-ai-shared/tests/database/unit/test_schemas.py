from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from transcribe_ai_shared.database import (
    JobStatus,
    JobType,
    TranscriptionJob,
    TranscriptionJobSchema,
    TranscriptionResult,
    TranscriptionResultSchema,
)


pytestmark = pytest.mark.unit


def valid_job_data() -> dict:
    now = datetime.now(timezone.utc)
    return {
        "job_uuid": uuid4(),
        "status": "QUEUED",
        "job_type": "FAST",
        "audio_uri": "audio/job.wav",
        "dispatch_required": True,
        "last_dispatched_at": None,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "attempt_count": 0,
        "last_error": None,
        "lease_owner": None,
        "lease_expires_at": None,
    }


def valid_result_data() -> dict:
    return {
        "job_uuid": uuid4(),
        "result": {
            "text": "Bonjour",
            "segments": [{"start": 0.0, "end": 1.0}],
        },
        "speaker_count": 1,
        "model_name": "whisper",
        "model_version": "large-v3",
        "note": "Transcription relue",
        "created_at": datetime.now(timezone.utc),
    }


def test_transcription_job_schema_validates_and_serializes_enums():
    schema = TranscriptionJobSchema.model_validate(valid_job_data())

    assert isinstance(schema.job_uuid, UUID)
    assert schema.status is JobStatus.QUEUED
    assert schema.job_type is JobType.FAST
    assert schema.dispatch_required is True
    assert schema.last_dispatched_at is None
    assert schema.model_dump(mode="json")["status"] == "QUEUED"
    assert schema.model_dump(mode="json")["job_type"] == "FAST"


def test_transcription_job_schema_accepts_omitted_last_dispatched_at():
    payload = valid_job_data()
    payload.pop("last_dispatched_at")

    schema = TranscriptionJobSchema.model_validate(payload)

    assert schema.last_dispatched_at is None


def test_transcription_job_schema_accepts_aware_last_dispatched_at():
    dispatched_at = datetime.now(timezone.utc)
    payload = valid_job_data()
    payload["last_dispatched_at"] = dispatched_at

    schema = TranscriptionJobSchema.model_validate(payload)

    assert schema.last_dispatched_at == dispatched_at


def test_transcription_result_schema_validates_json_payload():
    schema = TranscriptionResultSchema.model_validate(valid_result_data())

    assert schema.result["text"] == "Bonjour"
    assert schema.speaker_count == 1
    assert schema.note == "Transcription relue"
    assert schema.model_dump()["note"] == "Transcription relue"


@pytest.mark.parametrize("note", [None, "", "À vérifier"])
def test_transcription_result_schema_accepts_nullable_optional_note(note):
    payload = valid_result_data()
    payload["note"] = note

    schema = TranscriptionResultSchema.model_validate(payload)

    assert schema.note == note


def test_transcription_result_schema_accepts_omitted_note():
    payload = valid_result_data()
    payload.pop("note")

    schema = TranscriptionResultSchema.model_validate(payload)

    assert schema.note is None


@pytest.mark.parametrize(
    ("schema_class", "model_class", "payload"),
    [
        (TranscriptionJobSchema, TranscriptionJob, valid_job_data()),
        (TranscriptionResultSchema, TranscriptionResult, valid_result_data()),
    ],
)
def test_schemas_validate_sqlalchemy_models(schema_class, model_class, payload):
    model = model_class(**payload)

    schema = schema_class.model_validate(model)

    assert schema.model_dump()[next(iter(payload))] == payload[next(iter(payload))]


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("job_uuid", "not-a-uuid"),
        ("status", "PENDING"),
        ("job_type", "MONO_VOICE"),
        ("audio_uri", "   "),
        ("last_dispatched_at", datetime.now()),
        ("created_at", datetime.now()),
        ("attempt_count", -1),
    ],
)
def test_transcription_job_schema_rejects_invalid_values(
    field_name,
    invalid_value,
):
    payload = valid_job_data()
    payload[field_name] = invalid_value

    with pytest.raises(ValidationError):
        TranscriptionJobSchema.model_validate(payload)


def test_transcription_job_schema_rejects_incomplete_lease():
    payload = valid_job_data()
    payload["lease_owner"] = "worker-fast-1"

    with pytest.raises(ValidationError, match="doivent être renseignés ensemble"):
        TranscriptionJobSchema.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("result", {"invalid": {"not", "json"}}),
        ("speaker_count", -1),
        ("model_name", ""),
        ("model_version", "   "),
    ],
)
def test_transcription_result_schema_rejects_invalid_values(
    field_name,
    invalid_value,
):
    payload = valid_result_data()
    payload[field_name] = invalid_value

    with pytest.raises(ValidationError):
        TranscriptionResultSchema.model_validate(payload)


def test_schemas_reject_unknown_fields():
    payload = valid_job_data()
    payload["unexpected"] = True

    with pytest.raises(ValidationError) as error:
        TranscriptionJobSchema.model_validate(payload)

    assert error.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize(
    ("schema_class", "payload_factory", "field_name"),
    [
        (TranscriptionJobSchema, valid_job_data, "audio_uri"),
        (TranscriptionJobSchema, valid_job_data, "dispatch_required"),
        (TranscriptionResultSchema, valid_result_data, "result"),
    ],
)
def test_schemas_require_contract_fields(
    schema_class,
    payload_factory,
    field_name,
):
    payload = payload_factory()
    payload.pop(field_name)

    with pytest.raises(ValidationError) as error:
        schema_class.model_validate(payload)

    assert error.value.errors()[0]["type"] == "missing"


@pytest.mark.parametrize(
    ("schema_class", "payload_factory", "field_name"),
    [
        (TranscriptionJobSchema, valid_job_data, "lease_owner"),
        (TranscriptionResultSchema, valid_result_data, "model_name"),
    ],
)
def test_varchar_schemas_enforce_database_length(
    schema_class,
    payload_factory,
    field_name,
):
    payload = payload_factory()
    if field_name == "lease_owner":
        payload["lease_expires_at"] = datetime.now(timezone.utc)
    payload[field_name] = "x" * 255

    schema_class.model_validate(payload)

    payload[field_name] = "x" * 256
    with pytest.raises(ValidationError):
        schema_class.model_validate(payload)


@pytest.mark.parametrize("invalid_float", [float("nan"), float("inf"), float("-inf")])
def test_result_schema_rejects_non_finite_json_numbers(invalid_float):
    payload = valid_result_data()
    payload["result"] = {"confidence": invalid_float}

    with pytest.raises(ValidationError):
        TranscriptionResultSchema.model_validate(payload)
