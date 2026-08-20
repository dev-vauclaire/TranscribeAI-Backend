from collections.abc import Iterator

import pytest
from sqlalchemy import delete

from transcribe_ai_shared import (
    SessionFactory,
    TranscriptionJob,
    TranscriptionResult,
)


def _delete_transcription_rows(session_factory: SessionFactory) -> None:
    """Supprime les enfants avant les jobs afin de respecter les clés étrangères."""
    with session_factory.begin() as session:
        session.execute(delete(TranscriptionResult))
        session.execute(delete(TranscriptionJob))


@pytest.fixture(autouse=True)
def clean_database(session_factory: SessionFactory) -> Iterator[None]:
    """Isole les scénarios sans contourner les migrations Alembic racine."""
    _delete_transcription_rows(session_factory)
    yield
    _delete_transcription_rows(session_factory)
