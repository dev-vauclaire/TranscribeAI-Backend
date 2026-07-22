from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import ANY, Mock, call
from uuid import uuid4

import pytest

import mono_voice_worker.worker_mono_voice as worker_module
from mono_voice_worker.client_whisper import WhisperClientError, WhisperPayload
from mono_voice_worker.worker_mono_voice import WorkerMonoVoice
from transcribe_ai_shared import JobStatus


pytestmark = pytest.mark.unit


@pytest.fixture
def worker_dependencies(monkeypatch):
    audio_manager = Mock()
    monkeypatch.setattr(
        worker_module,
        "AudioManager",
        Mock(return_value=audio_manager),
    )
    sleep = Mock()
    monkeypatch.setattr(worker_module.time, "sleep", sleep)

    settings = SimpleNamespace(
        audio_folder_path="/tmp/test-audio",
        worker_loop_sleep_time=0,
    )
    session_factory = Mock()
    redis_queue_service = Mock()
    whisper_client = Mock()
    worker = WorkerMonoVoice(
        settings=settings,
        session_factory=session_factory,
        redis_queue_service=redis_queue_service,
        client_whisper=whisper_client,
    )

    return SimpleNamespace(
        audio_manager=audio_manager,
        redis_queue_service=redis_queue_service,
        session_factory=session_factory,
        sleep=sleep,
        whisper_client=whisper_client,
        worker=worker,
    )


def configure_repository(monkeypatch, job):
    repository = Mock()
    repository.get_by_uuid.return_value = job
    repository_class = Mock(return_value=repository)
    monkeypatch.setattr(worker_module, "JobRepository", repository_class)

    @contextmanager
    def fake_transaction(_session_factory):
        yield Mock()

    transaction = Mock(side_effect=fake_transaction)
    monkeypatch.setattr(worker_module, "transaction", transaction)
    return repository, transaction


def test_run_once_has_no_side_effect_when_queue_is_empty(worker_dependencies):
    worker_dependencies.redis_queue_service.pop_job.return_value = None

    result = worker_dependencies.worker.run_once()

    assert result is None
    worker_dependencies.session_factory.assert_not_called()
    worker_dependencies.whisper_client.send_to_whisper_service.assert_not_called()
    worker_dependencies.audio_manager.open_audio.assert_not_called()
    worker_dependencies.audio_manager.delete_audio.assert_not_called()
    worker_dependencies.sleep.assert_not_called()


def test_run_once_ignores_invalid_job_uuid(monkeypatch, worker_dependencies):
    worker_dependencies.redis_queue_service.pop_job.return_value = "not-a-uuid"
    transaction = Mock()
    monkeypatch.setattr(worker_module, "transaction", transaction)

    worker_dependencies.worker.run_once()

    transaction.assert_not_called()
    worker_dependencies.audio_manager.open_audio.assert_not_called()
    worker_dependencies.whisper_client.send_to_whisper_service.assert_not_called()
    worker_dependencies.audio_manager.delete_audio.assert_not_called()
    worker_dependencies.sleep.assert_not_called()


def test_run_once_completes_job_and_deletes_audio(monkeypatch, worker_dependencies):
    job_uuid = uuid4()
    job = SimpleNamespace(id=42, uuid=job_uuid, filename="job.wav")
    repository, transaction = configure_repository(monkeypatch, job)
    worker_dependencies.redis_queue_service.pop_job.return_value = str(job_uuid)
    worker_dependencies.audio_manager.open_audio.return_value = BytesIO(b"audio")
    payload = WhisperPayload(
        full_text="Bonjour",
        segments=[{"id": 0, "start": 0.0, "end": 1.0, "text": "Bonjour"}],
        language="fr",
    )
    worker_dependencies.whisper_client.send_to_whisper_service.return_value = payload

    worker_dependencies.worker.run_once()

    assert transaction.call_count == 2
    repository.get_by_uuid.assert_called_once_with(job_uuid)
    repository.update_status.assert_called_once_with(42, JobStatus.PROCESSING)
    worker_dependencies.audio_manager.open_audio.assert_called_once_with("job.wav")
    worker_dependencies.whisper_client.send_to_whisper_service.assert_called_once_with(
        ANY,
        filename="job.wav",
    )
    repository.complete_job.assert_called_once_with(
        42,
        result_data={
            "full_text": "Bonjour",
            "segments": [
                {"id": 0, "start": 0.0, "end": 1.0, "text": "Bonjour"},
            ],
            "language": "fr",
        },
    )
    worker_dependencies.audio_manager.delete_audio.assert_called_once_with("job.wav")
    worker_dependencies.sleep.assert_called_once_with(0)


def test_run_once_does_not_fail_unknown_job(monkeypatch, worker_dependencies):
    job_uuid = uuid4()
    repository, transaction = configure_repository(monkeypatch, None)
    worker_dependencies.redis_queue_service.pop_job.return_value = str(job_uuid)

    worker_dependencies.worker.run_once()

    transaction.assert_called_once_with(worker_dependencies.session_factory)
    repository.get_by_uuid.assert_called_once_with(job_uuid)
    repository.fail_job.assert_not_called()
    worker_dependencies.audio_manager.open_audio.assert_not_called()
    worker_dependencies.audio_manager.delete_audio.assert_not_called()
    worker_dependencies.sleep.assert_called_once_with(0)


def test_run_once_marks_missing_audio_as_failed(monkeypatch, worker_dependencies):
    job_uuid = uuid4()
    job = SimpleNamespace(id=42, uuid=job_uuid, filename="missing.wav")
    repository, transaction = configure_repository(monkeypatch, job)
    worker_dependencies.redis_queue_service.pop_job.return_value = str(job_uuid)
    worker_dependencies.audio_manager.open_audio.side_effect = FileNotFoundError(
        "missing.wav"
    )

    worker_dependencies.worker.run_once()

    assert transaction.call_count == 2
    repository.update_status.assert_called_once_with(42, JobStatus.PROCESSING)
    repository.fail_job.assert_called_once_with(
        42,
        error_msg="missing.wav",
        ended_at=ANY,
    )
    worker_dependencies.whisper_client.send_to_whisper_service.assert_not_called()
    worker_dependencies.audio_manager.delete_audio.assert_called_once_with(
        "missing.wav"
    )


def test_run_once_marks_whisper_error_as_failed(monkeypatch, worker_dependencies):
    job_uuid = uuid4()
    job = SimpleNamespace(id=42, uuid=job_uuid, filename="job.wav")
    repository, transaction = configure_repository(monkeypatch, job)
    worker_dependencies.redis_queue_service.pop_job.return_value = str(job_uuid)
    worker_dependencies.audio_manager.open_audio.return_value = BytesIO(b"audio")
    worker_dependencies.whisper_client.send_to_whisper_service.side_effect = (
        WhisperClientError("La transcription Whisper a échoué")
    )

    worker_dependencies.worker.run_once()

    assert transaction.call_count == 2
    assert repository.method_calls[:2] == [
        call.get_by_uuid(job_uuid),
        call.update_status(42, JobStatus.PROCESSING),
    ]
    repository.complete_job.assert_not_called()
    repository.fail_job.assert_called_once_with(
        42,
        error_msg="La transcription Whisper a échoué",
        ended_at=ANY,
    )
    worker_dependencies.audio_manager.delete_audio.assert_called_once_with("job.wav")
