import os
from collections.abc import AsyncIterator, Callable, Iterator
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.postgres import PostgresContainer

from migration.main import create_alembic_config
from transcribe_ai_shared.database.config import DatabaseSettings
from transcribe_ai_shared.database.engine import (
    create_async_db_engine,
    create_db_engine,
)
from transcribe_ai_shared.database.session import (
    SessionFactory,
    create_async_session_factory,
    create_session_factory,
)


def run_alembic_command(
    engine: Engine,
    config: Config,
    operation: Callable[[Config, str], None],
    target: str,
) -> None:
    """Exécute Alembic sur la connexion PostgreSQL fournie par Testcontainers."""
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        try:
            operation(config, target)
        finally:
            config.attributes.pop("connection", None)


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def postgres_url(postgres_container: PostgresContainer) -> str:
    return postgres_container.get_connection_url()


@pytest.fixture(scope="session")
def forbid_metadata_create_all() -> Iterator[None]:
    """Garantit que les intégrations communes construisent le schéma via Alembic."""
    original_create_all = MetaData.create_all

    def forbidden_create_all(*args, **kwargs) -> None:
        raise AssertionError("Database integration tests must use Alembic")

    MetaData.create_all = forbidden_create_all
    try:
        yield
    finally:
        MetaData.create_all = original_create_all


@pytest.fixture(scope="session")
def setup_db(
    postgres_url: str,
    forbid_metadata_create_all: None,
) -> Iterator[Engine]:
    """Applique puis retire les migrations autour des intégrations applicatives."""
    engine = create_db_engine(DatabaseSettings(url=postgres_url))
    config = create_alembic_config()
    upgraded = False
    try:
        run_alembic_command(engine, config, command.upgrade, "head")
        upgraded = True
        yield engine
    finally:
        if upgraded:
            run_alembic_command(engine, config, command.downgrade, "base")
        engine.dispose()


@pytest.fixture(scope="session")
def session_factory(setup_db: Engine) -> SessionFactory:
    return create_session_factory(setup_db)


@pytest_asyncio.fixture
async def async_session_factory(
    postgres_container: PostgresContainer,
    setup_db: Engine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Fournit des sessions async sur le schéma créé par Alembic."""
    settings = DatabaseSettings(url=postgres_container.get_connection_url())
    engine = create_async_db_engine(settings)
    factory = create_async_session_factory(engine)
    try:
        yield factory
    finally:
        await engine.dispose()


# Dialogue français entre deux interlocuteurs, par Hagindaz pour le Wikibook
# French, distribué sous CC BY-SA 3.0 / GFDL 1.2+ :
# https://commons.wikimedia.org/wiki/File:French_Dialogue_-_A_Formal_Conversation.ogg
_TEST_AUDIO_URL = (
    "https://upload.wikimedia.org/wikipedia/commons/c/c4/"
    "French_Dialogue_-_A_Formal_Conversation.ogg"
)
_TEST_AUDIO_SHA256 = "f700390a491a1af077d65fb29e0e7099a711324f6c1847308ba9dc74bc40ff1a"
_TEST_AUDIO_SIZE_BYTES = 66_291
_MAX_TEST_AUDIO_SIZE_BYTES = 1_048_576
_DOWNLOAD_TIMEOUT_SECONDS = 30
_DOWNLOAD_CHUNK_SIZE_BYTES = 64 * 1024


@pytest.fixture(scope="session")
def french_dialogue_audio_path(
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    """Télécharge et vérifie l'audio commun aux tests GPU réels."""
    if os.getenv("RUN_GPU_TESTS") != "1":
        pytest.skip("Les tests ML nécessitent RUN_GPU_TESTS=1")

    destination = (
        tmp_path_factory.mktemp("transcribe-ai-gpu-audio")
        / "french-formal-conversation.ogg"
    )
    _download_test_audio(destination)
    return destination


def _download_test_audio(destination: Path) -> None:
    partial_destination = destination.with_suffix(f"{destination.suffix}.part")
    digest = sha256()
    downloaded_size = 0

    try:
        with (
            httpx.stream(
                "GET",
                _TEST_AUDIO_URL,
                follow_redirects=False,
                timeout=_DOWNLOAD_TIMEOUT_SECONDS,
                headers={
                    "User-Agent": (
                        "TranscribeAI-Backend-GPU-Test/1.0 "
                        "(https://github.com/dev-vauclaire/TranscribeAI-Backend)"
                    )
                },
            ) as response,
            partial_destination.open("xb") as audio_file,
        ):
            if response.status_code != httpx.codes.OK:
                raise RuntimeError(
                    f"Le téléchargement audio a échoué avec HTTP {response.status_code}"
                )

            content_length = response.headers.get("Content-Length")
            if (
                content_length is not None
                and int(content_length) > _MAX_TEST_AUDIO_SIZE_BYTES
            ):
                raise ValueError("Le fichier audio distant dépasse la taille autorisée")

            for chunk in response.iter_bytes(_DOWNLOAD_CHUNK_SIZE_BYTES):
                downloaded_size += len(chunk)
                if downloaded_size > _MAX_TEST_AUDIO_SIZE_BYTES:
                    raise ValueError(
                        "Le fichier audio distant dépasse la taille autorisée"
                    )
                digest.update(chunk)
                audio_file.write(chunk)

        if downloaded_size != _TEST_AUDIO_SIZE_BYTES:
            raise ValueError(
                "La taille du fichier audio distant ne correspond pas à la fixture "
                "attendue"
            )
        if digest.hexdigest() != _TEST_AUDIO_SHA256:
            raise ValueError(
                "L'empreinte du fichier audio distant ne correspond pas à la fixture "
                "attendue"
            )

        partial_destination.replace(destination)
    except BaseException:
        partial_destination.unlink(missing_ok=True)
        raise
