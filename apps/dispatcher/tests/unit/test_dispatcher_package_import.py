import pytest

pytestmark = pytest.mark.unit


def test_package_import() -> None:
    import dispatcher

    assert dispatcher is not None
    assert dispatcher.DispatchJobSnapshot is not None
    assert dispatcher.DispatchReconciliationService is not None
    assert dispatcher.DispatchReconciliationStore is not None
    assert dispatcher.ReconciliationBatchResult is not None
    assert dispatcher.StaleDispatchSnapshot is not None
