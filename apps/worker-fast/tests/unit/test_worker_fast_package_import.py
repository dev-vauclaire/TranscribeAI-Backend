import pytest


pytestmark = pytest.mark.unit


def test_package_import() -> None:
    from worker_fast import WorkerFastSettings, run

    assert WorkerFastSettings is not None
    assert run is not None
