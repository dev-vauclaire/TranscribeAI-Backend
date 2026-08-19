from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import ANY, Mock, call
from uuid import uuid4

import pytest

import worker_fast.worker_mono_voice as worker_module
from worker_fast.client_whisper import WhisperClientError, WhisperPayload
from worker_fast.worker_mono_voice import WorkerMonoVoice, WorkerPayloadStatus
from transcribe_ai_shared import AudioLocation, AudioNotFoundError, JobStatus


pytestmark = pytest.mark.unit


@pytest.fixture
def worker_dependencies():
    audio_storage = Mock()
    session_factory = Mock()
    redis_queue_service = Mock()
    whisper_client = Mock()
    worker = WorkerMonoVoice(
        audio_storage=audio_storage,
        session_factory=session_factory,
        redis_queue_service=redis_queue_service,
        client_whisper=whisper_client,
    )

    return SimpleNamespace(
        audio_storage=audio_storage,
        redis_queue_service=redis_queue_service,
        session_factory=session_factory,
        whisper_client=whisper_client,
        worker=worker,
    )


def configure_repository(monkeypatch, job):
    repository = Mock()
    repository.get_by_uuid.return_value = job
    repository_class = Mock(return_value=repository)
    monkeypatch.setattr(
        worker_module,
        "LegacyTranscriptionJobRepository",
        repository_class,
    )

    @contextmanager
    def fake_transaction(_session_factory):
        yield Mock()

    transaction = Mock(side_effect=fake_transaction)
    monkeypatch.setattr(worker_module, "transaction", transaction)
    return repository, transaction


def test_run_once_has_no_side_effect_when_queue_is_empty(worker_dependencies):
    worker_dependencies.redis_queue_service.pop_job.return_value = None

    result = worker_dependencies.worker.run_once()

    assert result.status is WorkerPayloadStatus.IDLE
    assert result.job_uuid is None
    assert result.error_message is None
    worker_dependencies.session_factory.assert_not_called()
    worker_dependencies.whisper_client.send_to_whisper_service.assert_not_called()
    worker_dependencies.audio_storage.open.assert_not_called()
    worker_dependencies.audio_storage.delete.assert_not_called()


def test_run_once_ignores_invalid_job_uuid(monkeypatch, worker_dependencies):
    worker_dependencies.redis_queue_service.pop_job.return_value = "not-a-uuid"
    transaction = Mock()
    monkeypatch.setattr(worker_module, "transaction", transaction)

    result = worker_dependencies.worker.run_once()

    assert result.status is WorkerPayloadStatus.INVALID_JOB_UUID
    assert result.job_uuid is None
    assert result.error_message == "Identifiant de job invalide"
    transaction.assert_not_called()
    worker_dependencies.audio_storage.open.assert_not_called()
    worker_dependencies.whisper_client.send_to_whisper_service.assert_not_called()
    worker_dependencies.audio_storage.delete.assert_not_called()


def test_run_once_completes_job_and_deletes_audio(monkeypatch, worker_dependencies):
    job_uuid = uuid4()
    audio_location = AudioLocation(f"{job_uuid}/input.wav")
    job = SimpleNamespace(job_uuid=job_uuid, audio_uri=audio_location.uri)
    repository, transaction = configure_repository(monkeypatch, job)
    worker_dependencies.redis_queue_service.pop_job.return_value = str(job_uuid)
    worker_dependencies.audio_storage.open.return_value = BytesIO(b"audio")
    payload = WhisperPayload(
        full_text="Bonjour",
        segments=[{"id": 0, "start": 0.0, "end": 1.0, "text": "Bonjour"}],
        language="fr",
    )
    worker_dependencies.whisper_client.send_to_whisper_service.return_value = payload

    result = worker_dependencies.worker.run_once()

    assert result.status is WorkerPayloadStatus.COMPLETED
    assert result.job_uuid == job_uuid
    assert result.error_message is None
    assert transaction.call_count == 2
    repository.get_by_uuid.assert_called_once_with(job_uuid)
    repository.update_status.assert_called_once_with(job_uuid, JobStatus.PROCESSING)
    worker_dependencies.audio_storage.open.assert_called_once_with(audio_location)
    worker_dependencies.whisper_client.send_to_whisper_service.assert_called_once_with(
        ANY,
        filename=audio_location.uri,
    )
    repository.complete_job.assert_called_once_with(
        job_uuid,
        result_data={
            "full_text": "Bonjour",
            "segments": [
                {"id": 0, "start": 0.0, "end": 1.0, "text": "Bonjour"},
            ],
            "language": "fr",
        },
    )
    worker_dependencies.audio_storage.delete.assert_called_once_with(audio_location)


def test_run_once_does_not_fail_unknown_job(monkeypatch, worker_dependencies):
    job_uuid = uuid4()
    repository, transaction = configure_repository(monkeypatch, None)
    worker_dependencies.redis_queue_service.pop_job.return_value = str(job_uuid)

    result = worker_dependencies.worker.run_once()

    assert result.status is WorkerPayloadStatus.JOB_NOT_FOUND
    assert result.job_uuid == job_uuid
    assert str(job_uuid) in result.error_message
    transaction.assert_called_once_with(worker_dependencies.session_factory)
    repository.get_by_uuid.assert_called_once_with(job_uuid)
    repository.fail_job.assert_not_called()
    worker_dependencies.audio_storage.open.assert_not_called()
    worker_dependencies.audio_storage.delete.assert_not_called()


def test_run_once_marks_missing_audio_as_failed(monkeypatch, worker_dependencies):
    job_uuid = uuid4()
    audio_location = AudioLocation(f"{job_uuid}/input.wav")
    job = SimpleNamespace(job_uuid=job_uuid, audio_uri=audio_location.uri)
    repository, transaction = configure_repository(monkeypatch, job)
    worker_dependencies.redis_queue_service.pop_job.return_value = str(job_uuid)
    worker_dependencies.audio_storage.open.side_effect = AudioNotFoundError(
        audio_location.uri
    )

    result = worker_dependencies.worker.run_once()

    assert result.status is WorkerPayloadStatus.FAILED
    assert result.job_uuid == job_uuid
    assert result.error_message == audio_location.uri
    assert transaction.call_count == 2
    repository.update_status.assert_called_once_with(job_uuid, JobStatus.PROCESSING)
    repository.fail_job.assert_called_once_with(
        job_uuid,
        error_message=audio_location.uri,
        completed_at=ANY,
    )
    worker_dependencies.whisper_client.send_to_whisper_service.assert_not_called()
    worker_dependencies.audio_storage.delete.assert_called_once_with(audio_location)


def test_run_once_marks_invalid_audio_location_as_failed(
    monkeypatch,
    worker_dependencies,
):
    job_uuid = uuid4()
    job = SimpleNamespace(job_uuid=job_uuid, audio_uri="../outside.wav")
    repository, transaction = configure_repository(monkeypatch, job)
    worker_dependencies.redis_queue_service.pop_job.return_value = str(job_uuid)

    result = worker_dependencies.worker.run_once()

    assert result.status is WorkerPayloadStatus.FAILED
    assert result.job_uuid == job_uuid
    assert result.error_message is not None
    assert transaction.call_count == 2
    repository.update_status.assert_called_once_with(job_uuid, JobStatus.PROCESSING)
    repository.fail_job.assert_called_once_with(
        job_uuid,
        error_message=result.error_message,
        completed_at=ANY,
    )
    worker_dependencies.audio_storage.open.assert_not_called()
    worker_dependencies.audio_storage.delete.assert_not_called()


def test_run_once_rejects_an_audio_location_owned_by_another_job(
    monkeypatch,
    worker_dependencies,
):
    job_uuid = uuid4()
    other_job_location = AudioLocation(f"{uuid4()}/input.wav")
    job = SimpleNamespace(job_uuid=job_uuid, audio_uri=other_job_location.uri)
    repository, transaction = configure_repository(monkeypatch, job)
    worker_dependencies.redis_queue_service.pop_job.return_value = str(job_uuid)

    result = worker_dependencies.worker.run_once()

    assert result.status is WorkerPayloadStatus.FAILED
    assert result.job_uuid == job_uuid
    assert result.error_message == (
        "La localisation audio ne correspond pas au job traité."
    )
    assert transaction.call_count == 2
    repository.update_status.assert_called_once_with(job_uuid, JobStatus.PROCESSING)
    repository.fail_job.assert_called_once_with(
        job_uuid,
        error_message=result.error_message,
        completed_at=ANY,
    )
    worker_dependencies.audio_storage.open.assert_not_called()
    worker_dependencies.audio_storage.delete.assert_not_called()


def test_run_once_marks_whisper_error_as_failed(monkeypatch, worker_dependencies):
    job_uuid = uuid4()
    audio_location = AudioLocation(f"{job_uuid}/input.wav")
    job = SimpleNamespace(job_uuid=job_uuid, audio_uri=audio_location.uri)
    repository, transaction = configure_repository(monkeypatch, job)
    worker_dependencies.redis_queue_service.pop_job.return_value = str(job_uuid)
    worker_dependencies.audio_storage.open.return_value = BytesIO(b"audio")
    worker_dependencies.whisper_client.send_to_whisper_service.side_effect = (
        WhisperClientError("La transcription Whisper a échoué")
    )

    result = worker_dependencies.worker.run_once()

    assert result.status is WorkerPayloadStatus.FAILED
    assert result.job_uuid == job_uuid
    assert result.error_message == "La transcription Whisper a échoué"
    assert transaction.call_count == 2
    assert repository.method_calls[:2] == [
        call.get_by_uuid(job_uuid),
        call.update_status(job_uuid, JobStatus.PROCESSING),
    ]
    repository.complete_job.assert_not_called()
    repository.fail_job.assert_called_once_with(
        job_uuid,
        error_message="La transcription Whisper a échoué",
        completed_at=ANY,
    )
    worker_dependencies.audio_storage.delete.assert_called_once_with(audio_location)
