import pytest


pytestmark = pytest.mark.unit


def test_package_import() -> None:
    from worker_batch import WorkerBatchSettings, run

    assert WorkerBatchSettings is not None
    assert run is not None
