from typing import BinaryIO
from unittest.mock import Mock

from mono_voice_worker.client_whisper import ClientWhisper


class TrackingAudioStream:
    def __init__(self, audio_file: BinaryIO) -> None:
        self.audio_file = audio_file
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self.audio_file.read(size)

    def fileno(self) -> int:
        return self.audio_file.fileno()

    def tell(self) -> int:
        return self.audio_file.tell()


def test_send_to_whisper_streams_multipart_audio(monkeypatch, tmp_path):
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
    audio_path = tmp_path / "job.wav"
    audio_path.write_bytes(b"audio content")

    with audio_path.open("rb") as audio_file:
        audio_stream = TrackingAudioStream(audio_file)
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
