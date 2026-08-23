from pydantic import ValidationError
import pytest

from worker_long_form_diarization.config import WorkerLongFormDiarizationSettings


pytestmark = pytest.mark.unit

SETTING_ENV_NAMES = (
    "WORKER_ID",
    "WORKER_CONSUMER_GROUP",
    "WORKER_BLOCK_MILLISECONDS",
    "WORKER_AUTOCLAIM_INTERVAL_SECONDS",
    "WORKER_AUTOCLAIM_MIN_IDLE_MILLISECONDS",
    "WORKER_LEASE_SECONDS",
    "WORKER_HEARTBEAT_SECONDS",
    "MAX_ATTEMPTS",
    "WORKER_ENVIRONMENT",
    "WORKER_TRANSCRIBER_BACKEND",
    "WORKER_TRANSCRIBER_MODEL",
    "WORKER_TRANSCRIBER_DEVICE",
    "WORKER_TRANSCRIBER_COMPUTE_TYPE",
    "WORKER_TRANSCRIBER_BATCH_SIZE",
    "WORKER_TRANSCRIBER_HUGGING_FACE_TOKEN",
    "WORKER_TRANSCRIBER_MODEL_DIRECTORY",
    "WORKER_TRANSCRIBER_DIARIZATION_MODEL",
)


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for environment_name in SETTING_ENV_NAMES:
        monkeypatch.delenv(environment_name, raising=False)


def test_settings_default_to_the_whisperx_diarization_backend() -> None:
    settings = WorkerLongFormDiarizationSettings(
        worker_id="long-form-diarization-1",
        worker_transcriber_hugging_face_token="hf_test",
    )

    assert settings.worker_environment == "production"
    assert settings.worker_transcriber_backend == "whisperx"
    assert settings.worker_transcriber_model == "large-v3-turbo"
    assert settings.worker_transcriber_device == "cuda"
    assert settings.worker_transcriber_compute_type == "default"
    assert settings.worker_transcriber_batch_size == 16
    assert settings.worker_transcriber_model_directory.as_posix() == "/models"
    assert (
        settings.worker_transcriber_diarization_model
        == "pyannote/speaker-diarization-community-1"
    )
    assert settings.worker_consumer_group == "transcription-workers"
    assert settings.worker_block_milliseconds == 5_000
    assert settings.worker_autoclaim_interval_seconds == 60
    assert settings.worker_autoclaim_min_idle_milliseconds == 300_000
    assert settings.worker_heartbeat_seconds == 60


def test_settings_read_worker_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_ID", "long-form-diarization-env")
    monkeypatch.setenv("WORKER_CONSUMER_GROUP", "long-form-diarization-workers")
    monkeypatch.setenv("WORKER_BLOCK_MILLISECONDS", "250")
    monkeypatch.setenv("WORKER_AUTOCLAIM_INTERVAL_SECONDS", "20")
    monkeypatch.setenv("WORKER_AUTOCLAIM_MIN_IDLE_MILLISECONDS", "60000")
    monkeypatch.setenv("WORKER_LEASE_SECONDS", "120")
    monkeypatch.setenv("WORKER_HEARTBEAT_SECONDS", "30")
    monkeypatch.setenv("MAX_ATTEMPTS", "5")
    monkeypatch.setenv("WORKER_ENVIRONMENT", "production")
    monkeypatch.setenv("WORKER_TRANSCRIBER_BACKEND", "whisperx")
    monkeypatch.setenv("WORKER_TRANSCRIBER_MODEL", "large-v3")
    monkeypatch.setenv("WORKER_TRANSCRIBER_DEVICE", "cpu")
    monkeypatch.setenv("WORKER_TRANSCRIBER_COMPUTE_TYPE", "int8")
    monkeypatch.setenv("WORKER_TRANSCRIBER_BATCH_SIZE", "8")
    monkeypatch.setenv("WORKER_TRANSCRIBER_HUGGING_FACE_TOKEN", "hf_secret")
    monkeypatch.setenv("WORKER_TRANSCRIBER_MODEL_DIRECTORY", "/cache/models")

    settings = WorkerLongFormDiarizationSettings()

    assert settings.worker_id == "long-form-diarization-env"
    assert settings.worker_consumer_group == "long-form-diarization-workers"
    assert settings.worker_block_milliseconds == 250
    assert settings.worker_autoclaim_interval_seconds == 20
    assert settings.worker_autoclaim_min_idle_milliseconds == 60_000
    assert settings.worker_lease_seconds == 120
    assert settings.worker_heartbeat_seconds == 30
    assert settings.max_attempts == 5
    assert settings.worker_environment == "production"
    assert settings.worker_transcriber_backend == "whisperx"
    assert settings.worker_transcriber_model == "large-v3"
    assert settings.worker_transcriber_device == "cpu"
    assert settings.worker_transcriber_compute_type == "int8"
    assert settings.worker_transcriber_batch_size == 8
    assert (
        settings.worker_transcriber_hugging_face_token is not None
        and settings.worker_transcriber_hugging_face_token.get_secret_value()
        == "hf_secret"
    )
    assert settings.worker_transcriber_model_directory.as_posix() == "/cache/models"


def test_fake_transcriber_is_rejected_outside_development() -> None:
    with pytest.raises(ValidationError, match="réservé"):
        WorkerLongFormDiarizationSettings(
            worker_id="long-form-diarization-1",
            worker_environment="production",
            worker_transcriber_backend="fake",
        )


def test_fake_transcriber_does_not_require_a_hugging_face_token() -> None:
    settings = WorkerLongFormDiarizationSettings(
        worker_id="long-form-diarization-1",
        worker_environment="development",
        worker_transcriber_backend="fake",
    )

    assert settings.worker_transcriber_hugging_face_token is None


@pytest.mark.parametrize("token", [None, "", "   "])
def test_whisperx_requires_a_non_empty_hugging_face_token(
    token: str | None,
) -> None:
    with pytest.raises(ValidationError, match="HUGGING_FACE_TOKEN"):
        WorkerLongFormDiarizationSettings(
            worker_id="long-form-diarization-1",
            worker_transcriber_hugging_face_token=token,
        )


def test_hugging_face_token_is_masked_from_settings_representations() -> None:
    token = "hf_never_expose_this_token"
    settings = WorkerLongFormDiarizationSettings(
        worker_id="long-form-diarization-1",
        worker_transcriber_hugging_face_token=token,
    )

    assert token not in repr(settings)
    assert token not in str(settings)
    assert settings.worker_transcriber_hugging_face_token is not None
    assert str(settings.worker_transcriber_hugging_face_token) == "**********"


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("worker_transcriber_model", "small"),
        ("worker_transcriber_device", "tpu"),
        ("worker_transcriber_compute_type", "float64"),
        ("worker_transcriber_batch_size", 0),
        ("worker_transcriber_batch_size", 257),
        (
            "worker_transcriber_diarization_model",
            "pyannote/speaker-diarization-3.1",
        ),
    ],
)
def test_settings_reject_unsupported_engine_options(
    field_name: str,
    invalid_value: str | int,
) -> None:
    with pytest.raises(ValidationError):
        WorkerLongFormDiarizationSettings(
            worker_id="long-form-diarization-1",
            worker_transcriber_hugging_face_token="hf_test",
            **{field_name: invalid_value},
        )


def test_model_directory_must_be_absolute() -> None:
    with pytest.raises(ValidationError, match="chemin absolu"):
        WorkerLongFormDiarizationSettings(
            worker_id="long-form-diarization-1",
            worker_transcriber_hugging_face_token="hf_test",
            worker_transcriber_model_directory="relative/models",
        )
