import pytest


pytestmark = pytest.mark.unit


def test_package_import() -> None:
    import maintenance
    import transcribe_ai_shared

    assert maintenance is not None
    assert transcribe_ai_shared is not None
