import pytest

pytestmark = pytest.mark.unit


def test_package_import() -> None:
    import transcribe_ai_shared

    assert transcribe_ai_shared is not None
