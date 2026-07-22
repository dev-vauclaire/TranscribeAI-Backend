from unittest.mock import Mock

import pytest
from sqlalchemy import Engine

from transcribe_ai_shared.database.session import create_session_factory


pytestmark = pytest.mark.unit


def test_create_session_factory_binds_engine_and_disables_implicit_behaviors():
    engine = Mock(spec=Engine)

    factory = create_session_factory(engine)

    assert factory.kw["bind"] is engine
    assert factory.kw["autoflush"] is False
    assert factory.kw["expire_on_commit"] is False
