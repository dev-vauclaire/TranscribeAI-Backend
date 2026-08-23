import pytest


pytestmark = pytest.mark.unit


def test_package_import() -> None:
    from worker_long_form_diarization import (
        WorkerLongFormDiarizationSettings,
        run,
    )

    assert WorkerLongFormDiarizationSettings is not None
    assert run is not None
