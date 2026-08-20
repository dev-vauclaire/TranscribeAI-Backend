from uuid import uuid4

import pytest

from transcribe_ai_shared import (
    JobType,
    ResultRepository,
    TranscriptionJob,
    TranscriptionResult,
    async_transaction,
)


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_get_by_job_uuid_returns_the_persisted_result(
    async_session_factory,
) -> None:
    job_uuid = uuid4()
    payload = {
        "text": "Bonjour tout le monde.",
        "segments": [{"start": 0.0, "end": 1.25, "text": "Bonjour."}],
    }
    async with async_transaction(async_session_factory) as session:
        session.add(
            TranscriptionJob(
                job_uuid=job_uuid,
                job_type=JobType.FAST,
                audio_uri=f"{job_uuid}/input.wav",
            )
        )
        await session.flush()
        session.add(TranscriptionResult(job_uuid=job_uuid, result=payload))

    async with async_session_factory() as session:
        saved_result = await ResultRepository(session).get_by_job_uuid(job_uuid)

    assert saved_result is not None
    assert saved_result.job_uuid == job_uuid
    assert saved_result.result == payload


async def test_get_by_job_uuid_returns_none_for_unknown_job(
    async_session_factory,
) -> None:
    async with async_session_factory() as session:
        saved_result = await ResultRepository(session).get_by_job_uuid(uuid4())

    assert saved_result is None
