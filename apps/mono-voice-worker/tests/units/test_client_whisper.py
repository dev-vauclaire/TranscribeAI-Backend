from io import BytesIO
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from requests.exceptions import RequestException

from mono_voice_worker.client_whisper import ClientWhisper, WhisperClientError


pytestmark = pytest.mark.unit


class TrackingAudioStream:
    def __init__(self, content: bytes) -> None:
        self.stream = BytesIO(content)
        self.read_sizes: list[int] = []

    @property
    def len(self) -> int:
        return len(self.stream.getbuffer()) - self.stream.tell()

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self.stream.read(size)

    def tell(self) -> int:
        return self.stream.tell()


def test_send_to_whisper_streams_multipart_audio(monkeypatch):
    response = Mock()
    response.json.return_value = {
        "full_text": "Bonjour",
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "text": "Bonjour"},
        ],
        "language": "fr",
    }
    captured_request = {}

    def fake_post(url, *, data, headers, timeout):
        captured_request.update(
            url=url,
            headers=headers,
            timeout=timeout,
        )
        body = bytearray()
        while chunk := data.read(8):
            body.extend(chunk)
        captured_request["body"] = bytes(body)
        return response

    monkeypatch.setattr(
        "mono_voice_worker.client_whisper.requests.post",
        fake_post,
    )
    audio_stream = TrackingAudioStream(b"audio content")
    payload = ClientWhisper(
        "http://whisper",
        timeout=12,
    ).send_to_whisper_service(
        audio_stream,
        filename="job.wav",
    )

    response.raise_for_status.assert_called_once_with()
    assert payload.full_text == "Bonjour"
    assert captured_request["url"] == "http://whisper/BatchTranscriptionService"
    assert captured_request["timeout"] == 12
    assert captured_request["headers"]["Content-Type"].startswith(
        "multipart/form-data; boundary="
    )
    assert b'filename="job.wav"' in captured_request["body"]
    assert b"audio content" in captured_request["body"]
    assert audio_stream.read_sizes
    assert -1 not in audio_stream.read_sizes


def test_healthcheck_uses_dedicated_timeout(monkeypatch):
    response = Mock()
    get = Mock(return_value=response)
    monkeypatch.setattr(
        "mono_voice_worker.client_whisper.requests.get",
        get,
    )

    ClientWhisper(
        "http://whisper",
        timeout=600,
        healthcheck_timeout=3,
    ).check_whisper_connection()

    get.assert_called_once_with("http://whisper/health", timeout=3)
    response.raise_for_status.assert_called_once_with()


def test_healthcheck_translates_http_error(monkeypatch):
    http_error = RequestException("connection refused")
    monkeypatch.setattr(
        "mono_voice_worker.client_whisper.requests.get",
        Mock(side_effect=http_error),
    )

    with pytest.raises(WhisperClientError) as error:
        ClientWhisper("http://whisper").check_whisper_connection()

    assert error.value.__cause__ is http_error


def test_send_translates_invalid_whisper_payload(monkeypatch):
    response = Mock()
    response.json.return_value = {"unexpected": "payload"}
    monkeypatch.setattr(
        "mono_voice_worker.client_whisper.requests.post",
        Mock(return_value=response),
    )

    with pytest.raises(WhisperClientError) as error:
        ClientWhisper("http://whisper").send_to_whisper_service(
            BytesIO(b"audio"),
            filename="job.wav",
        )

    response.raise_for_status.assert_called_once_with()
    assert isinstance(error.value.__cause__, ValidationError)


def test_send_translates_http_error(monkeypatch):
    http_error = RequestException("Whisper unavailable")
    monkeypatch.setattr(
        "mono_voice_worker.client_whisper.requests.post",
        Mock(side_effect=http_error),
    )

    with pytest.raises(WhisperClientError) as error:
        ClientWhisper("http://whisper").send_to_whisper_service(
            BytesIO(b"audio"),
            filename="job.wav",
        )

    assert error.value.__cause__ is http_error


def test_cancel_transcription_uses_request_timeout(monkeypatch):
    response = Mock(status_code=200)
    post = Mock(return_value=response)
    monkeypatch.setattr(
        "mono_voice_worker.client_whisper.requests.post",
        post,
    )

    result = ClientWhisper(
        "http://whisper",
        timeout=12,
    ).cancel_transcription("job-uuid")

    assert result is True
    post.assert_called_once_with(
        "http://whisper/cancel",
        json={"job_id": "job-uuid"},
        timeout=12,
    )
