from __future__ import annotations

from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
import subprocess

import pytest

import api.Media.ffprobe as ffprobe_module
from api.Media import (
    AudioFormat,
    AudioMetadata,
    FFprobeMediaProbe,
    parse_ffprobe_output,
)
from api.exceptions import (
    InvalidAudioFileError,
    MediaProbeUnavailableError,
    UnsupportedAudioCodecError,
    UnsupportedAudioFormatError,
)


pytestmark = pytest.mark.unit

_MISSING = object()


def _ffprobe_output(
    *,
    format_name: object = "wav",
    codec_name: object = "pcm_s16le",
    stream_duration: object = "1.250000",
    format_duration: object = "1.250000",
    streams: object = _MISSING,
) -> bytes:
    if streams is _MISSING:
        stream: dict[str, object] = {"codec_type": "audio"}
        if codec_name is not _MISSING:
            stream["codec_name"] = codec_name
        if stream_duration is not _MISSING:
            stream["duration"] = stream_duration
        streams = [stream]

    format_metadata: dict[str, object] = {}
    if format_name is not _MISSING:
        format_metadata["format_name"] = format_name
    if format_duration is not _MISSING:
        format_metadata["duration"] = format_duration

    return json.dumps(
        {
            "streams": streams,
            "format": format_metadata,
        }
    ).encode()


@pytest.mark.parametrize(
    ("format_name", "codec_name", "expected_format"),
    [
        ("wav", "pcm_s16le", AudioFormat.WAV),
        ("wav", "pcm_s24le", AudioFormat.WAV),
        ("wav", "pcm_s32le", AudioFormat.WAV),
        ("wav", "pcm_f32le", AudioFormat.WAV),
        ("mp3", "mp3", AudioFormat.MP3),
        ("ogg", "vorbis", AudioFormat.OGG),
        ("ogg", "opus", AudioFormat.OGG),
        ("mov,mp4,m4a,3gp,3g2,mj2", "aac", AudioFormat.M4A),
        ("mov,mp4,m4a,3gp,3g2,mj2", "alac", AudioFormat.M4A),
    ],
)
def test_parse_ffprobe_output_accepts_supported_format_codec_pairs(
    format_name: str,
    codec_name: str,
    expected_format: AudioFormat,
) -> None:
    metadata = parse_ffprobe_output(
        _ffprobe_output(
            format_name=format_name,
            codec_name=codec_name,
            stream_duration="12.345678",
        )
    )

    assert metadata == AudioMetadata(
        duration_seconds=Decimal("12.345678"),
        format=expected_format,
        codec=codec_name,
    )


def test_parse_ffprobe_output_prefers_the_selected_audio_stream_duration() -> None:
    metadata = parse_ffprobe_output(
        _ffprobe_output(
            stream_duration="4.25",
            format_duration="99.5",
        )
    )

    assert metadata.duration_seconds == Decimal("4.25")


@pytest.mark.parametrize("missing_stream_duration", [_MISSING, None, "", "N/A"])
def test_parse_ffprobe_output_falls_back_to_container_duration(
    missing_stream_duration: object,
) -> None:
    metadata = parse_ffprobe_output(
        _ffprobe_output(
            stream_duration=missing_stream_duration,
            format_duration="8.75",
        )
    )

    assert metadata.duration_seconds == Decimal("8.75")


@pytest.mark.parametrize(
    "streams",
    [
        [],
        [{"codec_type": "video", "codec_name": "h264", "duration": "2"}],
        "not-a-list",
    ],
)
def test_parse_ffprobe_output_rejects_media_without_an_audio_stream(
    streams: object,
) -> None:
    with pytest.raises(InvalidAudioFileError, match="aucune piste audio"):
        parse_ffprobe_output(_ffprobe_output(streams=streams))


@pytest.mark.parametrize("missing_duration", [_MISSING, None, "", "N/A"])
def test_parse_ffprobe_output_rejects_an_absent_duration(
    missing_duration: object,
) -> None:
    with pytest.raises(InvalidAudioFileError, match="durée.*absente"):
        parse_ffprobe_output(
            _ffprobe_output(
                stream_duration=missing_duration,
                format_duration=missing_duration,
            )
        )


@pytest.mark.parametrize(
    "invalid_duration",
    ["not-a-number", "NaN", "Infinity", "-Infinity", "0", "-0.001"],
)
def test_parse_ffprobe_output_rejects_an_invalid_duration(
    invalid_duration: str,
) -> None:
    with pytest.raises(InvalidAudioFileError, match="durée.*invalide"):
        parse_ffprobe_output(
            _ffprobe_output(
                stream_duration=invalid_duration,
                format_duration="10",
            )
        )


@pytest.mark.parametrize("format_name", [_MISSING, None, ""])
def test_parse_ffprobe_output_rejects_a_missing_container(
    format_name: object,
) -> None:
    with pytest.raises(InvalidAudioFileError, match="conteneur"):
        parse_ffprobe_output(_ffprobe_output(format_name=format_name))


def test_parse_ffprobe_output_rejects_an_unsupported_container() -> None:
    with pytest.raises(UnsupportedAudioFormatError):
        parse_ffprobe_output(_ffprobe_output(format_name="flac", codec_name="flac"))


@pytest.mark.parametrize(
    ("format_name", "codec_name"),
    [
        ("wav", "adpcm_ms"),
        ("mp3", "aac"),
        ("ogg", "flac"),
        ("mov,mp4,m4a,3gp,3g2,mj2", "mp3"),
    ],
)
def test_parse_ffprobe_output_rejects_an_unsupported_codec(
    format_name: str,
    codec_name: str,
) -> None:
    with pytest.raises(UnsupportedAudioCodecError):
        parse_ffprobe_output(
            _ffprobe_output(format_name=format_name, codec_name=codec_name)
        )


@pytest.mark.parametrize("codec_name", [_MISSING, None, ""])
def test_parse_ffprobe_output_rejects_a_missing_codec(codec_name: object) -> None:
    with pytest.raises(InvalidAudioFileError, match="codec"):
        parse_ffprobe_output(_ffprobe_output(codec_name=codec_name))


@pytest.mark.parametrize(
    "invalid_output",
    [
        b"{not-json",
        b"\xff",
        b"[]",
        b'"not-an-object"',
    ],
)
def test_parse_ffprobe_output_reports_an_invalid_ffprobe_response(
    invalid_output: bytes,
) -> None:
    with pytest.raises(MediaProbeUnavailableError):
        parse_ffprobe_output(invalid_output)


class _GuardedReadStream(BytesIO):
    """Interdit une lecture sans borne afin de vérifier le contrat mémoire."""

    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.requested_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.requested_sizes.append(size)
        if size < 0:
            raise AssertionError("une lecture non bornée a été demandée")
        return super().read(size)


def _completed_probe(
    command: list[str],
    *,
    returncode: int = 0,
    stdout: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        args=command,
        returncode=returncode,
        stdout=stdout if stdout is not None else _ffprobe_output(),
        stderr=b"private ffprobe diagnostic",
    )


def test_probe_copies_by_chunks_and_invokes_ffprobe_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"a" * (2 * 1024 * 1024 + 17)
    source = _GuardedReadStream(content)
    observed_path: Path | None = None
    observed_command: list[str] | None = None
    observed_options: dict[str, object] | None = None

    def run_ffprobe(
        command: list[str],
        **options: object,
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal observed_path, observed_command, observed_options
        observed_command = command
        observed_options = options
        observed_path = Path(command[-1])
        assert observed_path.name == "input.media"
        assert observed_path.read_bytes() == content
        return _completed_probe(command)

    monkeypatch.setattr(ffprobe_module.subprocess, "run", run_ffprobe)

    metadata = FFprobeMediaProbe(
        ffprobe_path="custom-ffprobe",
        timeout_seconds=12.5,
    ).probe(source)

    assert metadata.format is AudioFormat.WAV
    assert source.closed is False
    assert source.requested_sizes
    assert set(source.requested_sizes) == {1024 * 1024}
    assert observed_path is not None
    assert not observed_path.exists()
    assert observed_command is not None
    assert observed_command[:9] == [
        "custom-ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type,codec_name,duration:format=format_name,duration",
        "-of",
        "json",
    ]
    assert observed_options == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "check": False,
        "shell": False,
        "timeout": 12.5,
    }


def test_probe_maps_a_nonzero_exit_to_invalid_audio_without_raw_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_media(
        command: list[str],
        **options: object,
    ) -> subprocess.CompletedProcess[bytes]:
        return _completed_probe(command, returncode=1, stdout=b"")

    monkeypatch.setattr(ffprobe_module.subprocess, "run", reject_media)

    with pytest.raises(InvalidAudioFileError) as captured:
        FFprobeMediaProbe().probe(BytesIO(b"not an audio file"))

    assert "private ffprobe diagnostic" not in str(captured.value)


def test_probe_maps_a_process_killed_by_signal_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ffprobe_module.subprocess,
        "run",
        lambda command, **options: _completed_probe(command, returncode=-9),
    )

    with pytest.raises(MediaProbeUnavailableError):
        FFprobeMediaProbe().probe(BytesIO(b"audio"))


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("/private/bin/ffprobe is absent"),
        subprocess.TimeoutExpired(cmd="/private/bin/ffprobe", timeout=3),
    ],
)
def test_probe_maps_process_failures_to_unavailable_and_cleans_temporary_file(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    observed_path: Path | None = None

    def fail_probe(command: list[str], **options: object) -> None:
        nonlocal observed_path
        observed_path = Path(command[-1])
        assert observed_path.exists()
        raise failure

    monkeypatch.setattr(ffprobe_module.subprocess, "run", fail_probe)

    with pytest.raises(MediaProbeUnavailableError) as captured:
        FFprobeMediaProbe(ffprobe_path="/private/bin/ffprobe").probe(BytesIO(b"audio"))

    assert "/private/bin" not in str(captured.value)
    assert observed_path is not None
    assert not observed_path.exists()


def test_probe_maps_temporary_copy_failure_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_copy(*args: object, **kwargs: object) -> None:
        raise OSError("temporary volume unavailable")

    monkeypatch.setattr(ffprobe_module, "copyfileobj", fail_copy)
    invoked = False

    def record_invocation(*args: object, **kwargs: object) -> None:
        nonlocal invoked
        invoked = True

    monkeypatch.setattr(ffprobe_module.subprocess, "run", record_invocation)

    with pytest.raises(MediaProbeUnavailableError) as captured:
        FFprobeMediaProbe().probe(BytesIO(b"audio"))

    assert "temporary volume unavailable" not in str(captured.value)
    assert invoked is False


@pytest.mark.parametrize(
    ("ffprobe_path", "timeout_seconds", "message"),
    [
        ("", 1.0, "ffprobe_path"),
        ("   ", 1.0, "ffprobe_path"),
        ("ffprobe", 0, "timeout_seconds"),
        ("ffprobe", -1, "timeout_seconds"),
        ("ffprobe", float("nan"), "timeout_seconds"),
        ("ffprobe", float("inf"), "timeout_seconds"),
    ],
)
def test_probe_rejects_invalid_configuration(
    ffprobe_path: str,
    timeout_seconds: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        FFprobeMediaProbe(
            ffprobe_path=ffprobe_path,
            timeout_seconds=timeout_seconds,
        )
