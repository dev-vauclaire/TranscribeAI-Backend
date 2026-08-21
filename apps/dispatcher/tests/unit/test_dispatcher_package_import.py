import pytest

pytestmark = pytest.mark.unit


def test_package_import() -> None:
    import dispatcher

    assert dispatcher is not None
