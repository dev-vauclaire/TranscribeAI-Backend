import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect

from .common import (
    get_current_revision,
    normalize_sql_expression,
    run_alembic_command,
)


pytestmark = pytest.mark.integration

PREVIOUS_REVISION_ID = "0001_initial_schema"
REVISION_ID = "0002_add_stale_dispatch_index"
INDEX_NAME = "idx_job_stale_dispatch"


def test_upgrade_0002_adds_stale_dispatch_index(
    database_engine: Engine,
    alembic_config: Config,
) -> None:
    run_alembic_command(
        database_engine,
        alembic_config,
        command.upgrade,
        PREVIOUS_REVISION_ID,
    )
    assert get_current_revision(database_engine) == PREVIOUS_REVISION_ID

    run_alembic_command(
        database_engine,
        alembic_config,
        command.upgrade,
        REVISION_ID,
    )

    assert get_current_revision(database_engine) == REVISION_ID
    indexes = {
        index["name"]: index
        for index in inspect(database_engine).get_indexes("transcription_jobs")
    }
    assert indexes.keys() == {
        "idx_job_dispatch",
        "idx_job_expired_lease",
        INDEX_NAME,
    }

    stale_dispatch_index = indexes[INDEX_NAME]
    assert stale_dispatch_index["column_names"] == [
        "last_dispatched_at",
        "job_uuid",
    ]
    assert stale_dispatch_index["unique"] is False
    predicate = normalize_sql_expression(
        str(stale_dispatch_index["dialect_options"]["postgresql_where"])
    )
    assert "status = 'queued'::transcription_job_status" in predicate
    assert "dispatch_required is false" in predicate
    assert "last_dispatched_at is not null" in predicate


def test_downgrade_0002_removes_only_stale_dispatch_index(
    database_engine: Engine,
    alembic_config: Config,
) -> None:
    run_alembic_command(
        database_engine,
        alembic_config,
        command.upgrade,
        REVISION_ID,
    )
    assert get_current_revision(database_engine) == REVISION_ID

    run_alembic_command(
        database_engine,
        alembic_config,
        command.downgrade,
        PREVIOUS_REVISION_ID,
    )

    assert get_current_revision(database_engine) == PREVIOUS_REVISION_ID
    indexes = {
        index["name"]
        for index in inspect(database_engine).get_indexes("transcription_jobs")
    }
    assert indexes == {"idx_job_dispatch", "idx_job_expired_lease"}
