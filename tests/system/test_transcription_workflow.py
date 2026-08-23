import asyncio
from datetime import timedelta
from io import BytesIO
from typing import Protocol
import wave
from uuid import UUID

from httpx import AsyncClient
from pydantic import JsonValue
import pytest
import redis.asyncio as redis_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dispatcher.application import run_dispatch_cycle
from dispatcher.config import DispatcherSettings
from transcribe_ai_shared import (
    AudioLocation,
    DatabaseSettings,
    FileSystemAudioStorage,
    JobRepository,
    JobStatus,
    JobStreamMessage,
    JobType,
    PostgresWorkerJobStore,
    RedisSettings,
    RedisTranscriptionStreams,
    ResultRepository,
    TranscriptionCompletionService,
    TranscriptionFailureService,
    TranscriptionJob,
    TranscriptionOutput,
    TranscriptionResult,
    WorkerClaimRejected,
    WorkerCompleted,
    WorkerProcessResult,
    WorkerRuntime,
    stream_name_for_job_type,
)
from transcribe_ai_shared.worker.testing import FakeTranscriber


pytestmark = [pytest.mark.system, pytest.mark.asyncio]

SYSTEM_TIMEOUT_SECONDS = 10
STATUS_POLL_TIMEOUT_SECONDS = 2
STATUS_POLL_INTERVAL_SECONDS = 0.01
WORKER_BLOCK_MILLISECONDS = 100
CONSUMER_GROUP = "transcription-system-workers"


class SystemRedisContext(Protocol):
    url: str
    streams: RedisTranscriptionStreams
    client: redis_asyncio.Redis


def _valid_wav_content() -> bytes:
    """Produit un WAV PCM court accepté par le vrai FFprobeMediaProbe."""
    sample_rate = 16_000
    frame_count = round(sample_rate * 0.1)
    destination = BytesIO()
    with wave.open(destination, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * frame_count)
    return destination.getvalue()


def _expected_result(job_type: JobType) -> dict[str, JsonValue]:
    return {
        "text": f"transcription système {job_type.value.lower()}",
        "mode": job_type.value,
    }


def _make_worker_runtime(
    *,
    job_type: JobType,
    worker_id: str,
    streams: RedisTranscriptionStreams,
    session_factory: async_sessionmaker[AsyncSession],
    transcriber: FakeTranscriber,
) -> WorkerRuntime:
    return WorkerRuntime(
        streams=streams,
        job_store=PostgresWorkerJobStore(
            session_factory,
            expected_job_type=job_type,
        ),
        transcriber=transcriber,
        completer=TranscriptionCompletionService(session_factory),
        failure_handler=TranscriptionFailureService(
            session_factory,
            max_attempts=3,
        ),
        job_type=job_type,
        group_name=CONSUMER_GROUP,
        worker_id=worker_id,
        lease_duration=timedelta(minutes=5),
    )


async def _wait_for_completed_response(
    client: AsyncClient,
    job_uuid: UUID,
) -> dict[str, object]:
    """Poll l'API avec une deadline monotone et retourne le premier COMPLETED."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + STATUS_POLL_TIMEOUT_SECONDS
    last_payload: object = None

    while True:
        response = await client.get(f"/api/transcriptions/{job_uuid}")
        assert response.status_code == 200
        last_payload = response.json()
        if (
            isinstance(last_payload, dict)
            and last_payload.get("status") == JobStatus.COMPLETED.value
        ):
            assert response.headers["cache-control"] == "no-store"
            return last_payload

        remaining_seconds = deadline - loop.time()
        if remaining_seconds <= 0:
            pytest.fail(
                f"Le job {job_uuid} n'est pas COMPLETED avant la deadline ; "
                f"dernière réponse={last_payload!r}"
            )
        await asyncio.sleep(min(STATUS_POLL_INTERVAL_SECONDS, remaining_seconds))


async def _load_persisted_state(
    session_factory: async_sessionmaker[AsyncSession],
    job_uuid: UUID,
) -> tuple[TranscriptionJob, TranscriptionResult | None, int]:
    async with session_factory() as session:
        job = await JobRepository(session).get_by_uuid(job_uuid)
        result = await ResultRepository(session).get_by_job_uuid(job_uuid)
        result_count = await session.scalar(
            select(func.count())
            .select_from(TranscriptionResult)
            .where(TranscriptionResult.job_uuid == job_uuid)
        )
    assert job is not None
    assert result_count is not None
    return job, result, result_count


@pytest.mark.parametrize(
    ("job_type", "duplicate_message"),
    [
        pytest.param(JobType.FAST, True, id="fast-with-duplicate"),
        pytest.param(
            JobType.LONG_FORM_DIARIZATION,
            False,
            id="long-form-diarization",
        ),
    ],
)
async def test_complete_transcription_workflow(
    job_type: JobType,
    duplicate_message: bool,
    system_api_client: AsyncClient,
    system_audio_storage: FileSystemAudioStorage,
    system_redis: SystemRedisContext,
    postgres_url: str,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with asyncio.timeout(SYSTEM_TIMEOUT_SECONDS):
        audio_content = _valid_wav_content()
        creation_response = await system_api_client.post(
            "/api/transcriptions",
            data={"type": job_type.value},
            files={
                "audio_file": (
                    "recording.wav",
                    audio_content,
                    "audio/wav",
                )
            },
        )

        assert creation_response.status_code == 202
        job_uuid = UUID(creation_response.json()["job_uuid"])
        assert creation_response.json() == {
            "job_uuid": str(job_uuid),
            "status": JobStatus.QUEUED.value,
        }
        assert creation_response.headers["location"] == (
            f"/api/transcriptions/{job_uuid}"
        )

        stored_audio = system_audio_storage.root / str(job_uuid) / "input.wav"
        assert stored_audio.is_file()
        assert stored_audio.read_bytes() == audio_content

        queued_job, queued_result, queued_result_count = await _load_persisted_state(
            async_session_factory,
            job_uuid,
        )
        assert queued_job.status is JobStatus.QUEUED
        assert queued_job.job_type is job_type
        assert queued_job.audio_uri == f"{job_uuid}/input.wav"
        assert queued_job.dispatch_required is True
        assert queued_job.attempt_count == 0
        assert queued_result is None
        assert queued_result_count == 0

        dispatch_cycle = await run_dispatch_cycle(
            DatabaseSettings(url=postgres_url),
            RedisSettings(redis_url=system_redis.url),
            DispatcherSettings(
                batch_size=10,
                reconciliation_timeout_seconds=300,
                max_attempts=3,
            ),
        )
        assert dispatch_cycle.recovery.selected_count == 0
        assert dispatch_cycle.reconciliation.selected_count == 0
        assert dispatch_cycle.dispatch.selected_count == 1
        assert dispatch_cycle.dispatch.published_count == 1
        assert dispatch_cycle.dispatch.confirmed_count == 1
        assert dispatch_cycle.dispatch.error_count == 0

        dispatched_job, _, _ = await _load_persisted_state(
            async_session_factory,
            job_uuid,
        )
        assert dispatched_job.status is JobStatus.QUEUED
        assert dispatched_job.dispatch_required is False
        assert dispatched_job.last_dispatched_at is not None
        assert dispatched_job.attempt_count == 0

        if duplicate_message:
            await system_redis.streams.publish(
                JobStreamMessage(
                    job_uuid=job_uuid,
                    job_type=job_type,
                    attempt_count=0,
                )
            )

        stream_name = stream_name_for_job_type(job_type)
        other_job_type = (
            JobType.LONG_FORM_DIARIZATION if job_type is JobType.FAST else JobType.FAST
        )
        other_stream_name = stream_name_for_job_type(other_job_type)
        expected_message_count = 2 if duplicate_message else 1
        entries = await system_redis.client.xrange(stream_name)
        assert len(entries) == expected_message_count
        assert len({message_id for message_id, _ in entries}) == expected_message_count
        assert [payload for _, payload in entries] == [
            {
                "job_uuid": str(job_uuid),
                "attempt_count": "0",
            }
        ] * expected_message_count
        assert await system_redis.client.xlen(other_stream_name) == 0

        expected_result = _expected_result(job_type)
        transcriber = FakeTranscriber(
            TranscriptionOutput(result=expected_result),
        )
        runtimes = [
            _make_worker_runtime(
                job_type=job_type,
                worker_id=f"system-{job_type.value.lower()}-worker-{index}",
                streams=system_redis.streams,
                session_factory=async_session_factory,
                transcriber=transcriber,
            )
            for index in range(expected_message_count)
        ]
        await asyncio.gather(*(runtime.initialize() for runtime in runtimes))
        worker_results: list[WorkerProcessResult] = list(
            await asyncio.gather(
                *(
                    runtime.process_next(
                        block_milliseconds=WORKER_BLOCK_MILLISECONDS,
                    )
                    for runtime in runtimes
                )
            )
        )

        completed_results = [
            result for result in worker_results if isinstance(result, WorkerCompleted)
        ]
        rejected_results = [
            result
            for result in worker_results
            if isinstance(result, WorkerClaimRejected)
        ]
        assert len(completed_results) == 1
        assert len(rejected_results) == int(duplicate_message)
        assert completed_results[0].removed_from_stream is True
        assert all(result.removed_from_stream for result in rejected_results)
        assert transcriber.calls == (AudioLocation(f"{job_uuid}/input.wav"),)

        (
            completed_job,
            persisted_result,
            persisted_result_count,
        ) = await _load_persisted_state(async_session_factory, job_uuid)
        assert completed_job.status is JobStatus.COMPLETED
        assert completed_job.attempt_count == 0
        assert completed_job.completed_at is not None
        assert persisted_result is not None
        assert persisted_result.result == expected_result
        assert persisted_result_count == 1

        pending = await system_redis.client.xpending(stream_name, CONSUMER_GROUP)
        assert pending["pending"] == 0
        assert await system_redis.client.xrange(stream_name) == []
        assert await system_redis.client.xlen(other_stream_name) == 0

        completed_payload = await _wait_for_completed_response(
            system_api_client,
            job_uuid,
        )
        assert completed_payload == {
            "job_uuid": str(job_uuid),
            "status": JobStatus.COMPLETED.value,
            "result": expected_result,
        }
        assert stored_audio.is_file()
        assert stored_audio.read_bytes() == audio_content
