import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from transcribe_ai_shared import (
    AudioLocation,
    ClaimedJob,
    JobRepository,
    JobStatus,
    JobType,
    ResultRepository,
    TranscriptionCompletionError,
    TranscriptionCompletionRejectedError,
    TranscriptionCompletionService,
    TranscriptionJob,
    TranscriptionOutput,
    TranscriptionResult,
    async_transaction,
)


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

JOB_UUID = UUID("12345678-1234-5678-1234-567812345678")
WORKER_ID = "worker-fast-1"
NOW = datetime(2026, 8, 21, 12, tzinfo=UTC)


def make_claimed_job(*, attempt_count: int = 3) -> ClaimedJob:
    return ClaimedJob(
        job_uuid=JOB_UUID,
        job_type=JobType.FAST,
        attempt_count=attempt_count,
        audio_location=AudioLocation(f"{JOB_UUID}/input.wav"),
    )


async def persist_processing_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    worker_id: str = WORKER_ID,
    attempt_count: int = 3,
) -> None:
    async with async_transaction(session_factory) as session:
        await JobRepository(session).add(
            TranscriptionJob(
                job_uuid=JOB_UUID,
                status=JobStatus.PROCESSING,
                job_type=JobType.FAST,
                audio_uri=f"{JOB_UUID}/input.wav",
                dispatch_required=False,
                attempt_count=attempt_count,
                lease_owner=worker_id,
                lease_expires_at=NOW + timedelta(minutes=5),
            )
        )


async def load_persisted_state(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[TranscriptionJob, TranscriptionResult | None]:
    async with session_factory() as session:
        job = await JobRepository(session).get_by_uuid(JOB_UUID)
        result = await ResultRepository(session).get_by_job_uuid(JOB_UUID)
        assert job is not None
        return job, result


async def test_complete_persists_jsonb_and_marks_job_completed_after_commit(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await persist_processing_job(async_session_factory)
    payload = {
        "text": "Bonjour tout le monde.",
        "segments": [
            {
                "start": 0.0,
                "end": 1.25,
                "text": "Bonjour.",
            }
        ],
    }
    service = TranscriptionCompletionService(async_session_factory)

    await service.complete(
        job=make_claimed_job(),
        worker_id=WORKER_ID,
        output=TranscriptionOutput(result=payload),
    )

    saved_job, saved_result = await load_persisted_state(async_session_factory)
    assert saved_job.status is JobStatus.COMPLETED
    assert saved_job.completed_at is not None
    assert saved_job.completed_at.tzinfo is not None
    assert saved_result is not None
    assert saved_result.job_uuid == JOB_UUID
    assert saved_result.result == payload
    assert saved_result.created_at.tzinfo is not None


async def test_complete_rolls_back_when_result_cannot_be_serialized(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await persist_processing_job(async_session_factory)
    service = TranscriptionCompletionService(async_session_factory)

    with pytest.raises(TranscriptionCompletionError) as raised:
        await service.complete(
            job=make_claimed_job(),
            worker_id=WORKER_ID,
            output=TranscriptionOutput(result={"invalid": object()}),  # type: ignore[dict-item]
        )

    assert isinstance(raised.value.__cause__, SQLAlchemyError)
    saved_job, saved_result = await load_persisted_state(async_session_factory)
    assert saved_job.status is JobStatus.PROCESSING
    assert saved_job.completed_at is None
    assert saved_result is None


async def test_complete_refuses_the_wrong_lease_owner_and_rolls_back_result(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await persist_processing_job(async_session_factory)
    service = TranscriptionCompletionService(async_session_factory)

    with pytest.raises(TranscriptionCompletionRejectedError) as raised:
        await service.complete(
            job=make_claimed_job(),
            worker_id="worker-fast-2",
            output=TranscriptionOutput(result={"text": "stale owner"}),
        )

    assert raised.value.job_uuid == JOB_UUID
    assert raised.value.worker_id == "worker-fast-2"
    assert raised.value.expected_attempt_count == 3
    saved_job, saved_result = await load_persisted_state(async_session_factory)
    assert saved_job.status is JobStatus.PROCESSING
    assert saved_job.lease_owner == WORKER_ID
    assert saved_job.completed_at is None
    assert saved_result is None


async def test_complete_refuses_a_stale_attempt_and_rolls_back_result(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await persist_processing_job(async_session_factory, attempt_count=3)
    service = TranscriptionCompletionService(async_session_factory)

    with pytest.raises(TranscriptionCompletionRejectedError) as raised:
        await service.complete(
            job=make_claimed_job(attempt_count=2),
            worker_id=WORKER_ID,
            output=TranscriptionOutput(result={"text": "stale attempt"}),
        )

    assert raised.value.job_uuid == JOB_UUID
    assert raised.value.worker_id == WORKER_ID
    assert raised.value.expected_attempt_count == 2
    saved_job, saved_result = await load_persisted_state(async_session_factory)
    assert saved_job.status is JobStatus.PROCESSING
    assert saved_job.attempt_count == 3
    assert saved_job.completed_at is None
    assert saved_result is None


class SynchronizedResultRepository(ResultRepository):
    """Force deux finalisations à atteindre l'INSERT avant de les départager."""

    def __init__(self, session: AsyncSession, barrier: asyncio.Barrier) -> None:
        super().__init__(session)
        self._barrier = barrier

    async def add(self, result: TranscriptionResult) -> None:
        await self._barrier.wait()
        await super().add(result)


async def test_two_concurrent_completions_persist_exactly_one_result(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await persist_processing_job(async_session_factory)
    barrier = asyncio.Barrier(2)
    service = TranscriptionCompletionService(
        async_session_factory,
        result_repository_factory=lambda session: SynchronizedResultRepository(
            session,
            barrier,
        ),
    )

    outcomes = await asyncio.wait_for(
        asyncio.gather(
            service.complete(
                job=make_claimed_job(),
                worker_id=WORKER_ID,
                output=TranscriptionOutput(result={"text": "first"}),
            ),
            service.complete(
                job=make_claimed_job(),
                worker_id=WORKER_ID,
                output=TranscriptionOutput(result={"text": "second"}),
            ),
            return_exceptions=True,
        ),
        timeout=10,
    )

    successes = [outcome for outcome in outcomes if outcome is None]
    failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], TranscriptionCompletionError)
    assert not isinstance(failures[0], TranscriptionCompletionRejectedError)
    assert isinstance(failures[0].__cause__, SQLAlchemyError)

    async with async_session_factory() as session:
        result_count = await session.scalar(
            select(func.count()).select_from(TranscriptionResult)
        )
        saved_job = await JobRepository(session).get_by_uuid(JOB_UUID)
        saved_result = await ResultRepository(session).get_by_job_uuid(JOB_UUID)

    assert result_count == 1
    assert saved_job is not None
    assert saved_job.status is JobStatus.COMPLETED
    assert saved_result is not None
    assert saved_result.result in ({"text": "first"}, {"text": "second"})


class FailingAfterUpdateJobRepository(JobRepository):
    """Simule une panne SQLAlchemy après le véritable UPDATE conditionnel."""

    async def mark_completed(
        self,
        job_uuid: UUID,
        worker_id: str,
        expected_attempt_count: int,
    ) -> bool:
        completed = await super().mark_completed(
            job_uuid,
            worker_id,
            expected_attempt_count,
        )
        assert completed is True
        raise SQLAlchemyError("failure after completion update")


async def test_sqlalchemy_error_after_update_rolls_back_result_and_job(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await persist_processing_job(async_session_factory)
    service = TranscriptionCompletionService(
        async_session_factory,
        job_repository_factory=FailingAfterUpdateJobRepository,
    )

    with pytest.raises(TranscriptionCompletionError) as raised:
        await service.complete(
            job=make_claimed_job(),
            worker_id=WORKER_ID,
            output=TranscriptionOutput(result={"text": "must be rolled back"}),
        )

    assert isinstance(raised.value.__cause__, SQLAlchemyError)
    saved_job, saved_result = await load_persisted_state(async_session_factory)
    assert saved_job.status is JobStatus.PROCESSING
    assert saved_job.completed_at is None
    assert saved_result is None
