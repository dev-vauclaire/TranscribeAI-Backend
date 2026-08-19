from collections.abc import Iterator

import pytest
from sqlalchemy import delete

from transcribe_ai_shared.database.models import (
    TranscriptionJob,
    TranscriptionResult,
)
from transcribe_ai_shared.database.session import SessionFactory


@pytest.fixture(autouse=True)
def clean_database(session_factory: SessionFactory) -> Iterator[None]:
    """Isole chaque scénario de cleanup sans recréer le schéma Alembic."""
    yield

    with session_factory.begin() as session:
        session.execute(delete(TranscriptionResult))
        session.execute(delete(TranscriptionJob))
