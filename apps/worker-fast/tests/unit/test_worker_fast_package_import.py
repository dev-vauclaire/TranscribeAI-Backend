import pytest


pytestmark = pytest.mark.unit


def test_package_import() -> None:
    from worker_fast import FasterWhisperTranscriber, WorkerFastSettings, run

    assert FasterWhisperTranscriber is not None
    assert WorkerFastSettings is not None
    assert run is not None
