from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from transcribe_ai_shared.database.models.enums import JobType
from transcribe_ai_shared.queue.exceptions import InvalidJobStreamMessageError
from transcribe_ai_shared.queue.models import (
    JobStreamMessage,
    MAX_ATTEMPT_COUNT,
    TranscriptionStreamName,
    stream_name_for_job_type,
)
from transcribe_ai_shared.queue.serialization import JobStreamMessageCodec


pytestmark = pytest.mark.unit

JOB_UUID = UUID("12345678-1234-5678-1234-567812345678")
REDIS_MESSAGE_ID = "1755684000000-0"


@pytest.mark.parametrize(
    ("job_type", "expected_stream"),
    [
        (JobType.FAST, TranscriptionStreamName.FAST),
        (JobType.BATCH, TranscriptionStreamName.BATCH),
    ],
)
def test_job_type_selects_its_only_allowed_stream(
    job_type: JobType,
    expected_stream: TranscriptionStreamName,
) -> None:
    assert stream_name_for_job_type(job_type) is expected_stream


def test_stream_mapping_rejects_an_untyped_value() -> None:
    with pytest.raises(ValueError, match="JobType"):
        stream_name_for_job_type("FAST")  # type: ignore[arg-type]


def test_serialization_omits_job_type_carried_by_stream() -> None:
    message = JobStreamMessage(
        job_uuid=JOB_UUID,
        job_type=JobType.FAST,
        attempt_count=3,
    )

    payload = JobStreamMessageCodec.serialize(message)

    assert payload == {
        "job_uuid": str(JOB_UUID),
        "attempt_count": "3",
    }


def test_deserialization_accepts_redis_byte_responses_and_ignores_extra_fields() -> (
    None
):
    payload = {
        b"job_uuid": str(JOB_UUID).encode(),
        b"attempt_count": b"2",
        b"future_field": b"ignored",
    }

    message = JobStreamMessageCodec.deserialize(
        REDIS_MESSAGE_ID.encode(),
        JobType.BATCH,
        payload,
    )

    assert message.redis_message_id == REDIS_MESSAGE_ID
    assert message.job_uuid == JOB_UUID
    assert message.job_type is JobType.BATCH
    assert message.attempt_count == 2


def test_deserialization_accepts_maximum_postgresql_integer() -> None:
    message = JobStreamMessageCodec.deserialize(
        REDIS_MESSAGE_ID,
        JobType.FAST,
        {
            "job_uuid": str(JOB_UUID),
            "attempt_count": str(MAX_ATTEMPT_COUNT),
        },
    )

    assert message.attempt_count == MAX_ATTEMPT_COUNT


@pytest.mark.parametrize(
    "invalid_job_uuid",
    [
        "not-an-uuid",
        "12345678123456781234567812345678",
        "12345678-1234-5678-1234-56781234567A",
    ],
)
def test_deserialization_rejects_invalid_or_non_canonical_uuid(
    invalid_job_uuid: str,
) -> None:
    with pytest.raises(InvalidJobStreamMessageError) as raised:
        JobStreamMessageCodec.deserialize(
            REDIS_MESSAGE_ID,
            JobType.FAST,
            {
                "job_uuid": invalid_job_uuid,
                "attempt_count": "0",
                "secret": "must-not-leak",
            },
        )

    assert raised.value.redis_message_id == REDIS_MESSAGE_ID
    assert REDIS_MESSAGE_ID in str(raised.value)
    assert "must-not-leak" not in str(raised.value)


@pytest.mark.parametrize(
    "invalid_attempt_count",
    ["", "-1", "+1", "01", "1.0", "True", "2147483648"],
)
def test_deserialization_rejects_non_canonical_attempt_count(
    invalid_attempt_count: str,
) -> None:
    with pytest.raises(InvalidJobStreamMessageError) as raised:
        JobStreamMessageCodec.deserialize(
            REDIS_MESSAGE_ID,
            JobType.FAST,
            {
                "job_uuid": str(JOB_UUID),
                "attempt_count": invalid_attempt_count,
            },
        )

    assert raised.value.redis_message_id == REDIS_MESSAGE_ID


@pytest.mark.parametrize("missing_field", ["job_uuid", "attempt_count"])
def test_deserialization_rejects_incomplete_payload(missing_field: str) -> None:
    payload = {
        "job_uuid": str(JOB_UUID),
        "attempt_count": "0",
    }
    payload.pop(missing_field)

    with pytest.raises(InvalidJobStreamMessageError) as raised:
        JobStreamMessageCodec.deserialize(
            REDIS_MESSAGE_ID,
            JobType.FAST,
            payload,
        )

    assert raised.value.redis_message_id == REDIS_MESSAGE_ID
    assert missing_field in str(raised.value)


def test_deserialization_rejects_duplicate_text_and_byte_keys() -> None:
    with pytest.raises(InvalidJobStreamMessageError, match="plusieurs fois"):
        JobStreamMessageCodec.deserialize(
            REDIS_MESSAGE_ID,
            JobType.FAST,
            {
                "job_uuid": str(JOB_UUID),
                b"job_uuid": str(JOB_UUID).encode(),
                "attempt_count": "0",
            },
        )


@pytest.mark.parametrize("invalid_attempt_count", [-1, True, MAX_ATTEMPT_COUNT + 1])
def test_outgoing_message_rejects_invalid_attempt_count(
    invalid_attempt_count: int,
) -> None:
    with pytest.raises(InvalidJobStreamMessageError):
        JobStreamMessage(
            job_uuid=JOB_UUID,
            job_type=JobType.FAST,
            attempt_count=invalid_attempt_count,
        )


def test_stream_message_is_immutable() -> None:
    message = JobStreamMessage(JOB_UUID, JobType.FAST, 0)

    with pytest.raises(FrozenInstanceError):
        message.attempt_count = 1  # type: ignore[misc]
