from collections.abc import Callable

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID

from migration.main import upgrade_database
from transcribe_ai_shared.database import DatabaseSettings


pytestmark = pytest.mark.integration

REVISION_ID = "0001_initial_schema"
APPLICATION_TABLES = {
    "outbox_events",
    "transcription_jobs",
    "transcription_results",
}


def run_alembic_command(
    engine: Engine,
    config: Config,
    operation: Callable[[Config, str], None],
    target: str,
) -> None:
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        try:
            operation(config, target)
        finally:
            config.attributes.pop("connection", None)


def get_current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def assert_migration_matches_metadata(engine: Engine, config: Config) -> None:
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        try:
            command.check(config)
        finally:
            config.attributes.pop("connection", None)


def get_application_tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


def normalize_sql_expression(expression: str) -> str:
    return " ".join(expression.lower().split())


def test_upgrade_0001_initial_schema_from_base(
    database_engine: Engine,
    database_settings: DatabaseSettings,
    alembic_config: Config,
) -> None:
    assert inspect(database_engine).get_table_names() == []
    assert get_current_revision(database_engine) is None

    upgrade_database(database_settings)

    inspector = inspect(database_engine)
    assert get_current_revision(database_engine) == REVISION_ID
    assert ScriptDirectory.from_config(alembic_config).get_current_head() == REVISION_ID
    assert get_application_tables(database_engine) == APPLICATION_TABLES

    job_columns = {
        column["name"]: column for column in inspector.get_columns("transcription_jobs")
    }
    result_columns = {
        column["name"]: column
        for column in inspector.get_columns("transcription_results")
    }
    assert isinstance(job_columns["job_uuid"]["type"], UUID)
    assert isinstance(job_columns["status"]["type"], ENUM)
    assert job_columns["status"]["type"].enums == [
        "QUEUED",
        "PROCESSING",
        "COMPLETED",
        "FAILED",
    ]
    assert isinstance(job_columns["job_type"]["type"], ENUM)
    assert job_columns["job_type"]["type"].enums == ["FAST", "BATCH"]
    assert isinstance(result_columns["result"]["type"], JSONB)
    assert result_columns["note"]["nullable"] is True

    assert inspector.get_pk_constraint("transcription_jobs")["constrained_columns"] == [
        "job_uuid"
    ]
    assert inspector.get_pk_constraint("outbox_events")["constrained_columns"] == [
        "event_uuid"
    ]
    assert inspector.get_pk_constraint("transcription_results")[
        "constrained_columns"
    ] == ["job_uuid"]

    for table_name in ("outbox_events", "transcription_results"):
        foreign_keys = inspector.get_foreign_keys(table_name)
        assert len(foreign_keys) == 1
        assert foreign_keys[0]["constrained_columns"] == ["job_uuid"]
        assert foreign_keys[0]["referred_table"] == "transcription_jobs"
        assert foreign_keys[0]["referred_columns"] == ["job_uuid"]
        assert foreign_keys[0]["options"] == {
            "ondelete": "RESTRICT",
            "onupdate": "RESTRICT",
        }

    expected_checks = {
        "transcription_jobs": {
            "attempt_count_non_negative": ("attempt_count >= 0",),
            "audio_uri_non_empty": ("length(btrim(audio_uri)) > 0",),
            "lease_fields_consistent": (
                "lease_owner is null",
                "lease_expires_at is null",
                "lease_owner is not null",
                "lease_expires_at is not null",
            ),
        },
        "outbox_events": {
            "attempt_count_non_negative": ("attempt_count >= 0",),
            "event_type_non_empty": ("length(btrim(event_type::text)) > 0",),
            "lock_fields_consistent": (
                "locked_at is null",
                "locked_by is null",
                "locked_at is not null",
                "locked_by is not null",
            ),
        },
        "transcription_results": {
            "model_name_non_empty": (
                "model_name is null",
                "length(btrim(model_name::text)) > 0",
            ),
            "model_version_non_empty": (
                "model_version is null",
                "length(btrim(model_version::text)) > 0",
            ),
            "speaker_count_non_negative": (
                "speaker_count is null",
                "speaker_count >= 0",
            ),
        },
    }
    for table_name, expected_constraints in expected_checks.items():
        actual_constraints = {
            constraint["name"]: normalize_sql_expression(constraint["sqltext"])
            for constraint in inspector.get_check_constraints(table_name)
        }
        assert actual_constraints.keys() == expected_constraints.keys()
        for constraint_name, expected_fragments in expected_constraints.items():
            for fragment in expected_fragments:
                assert fragment in actual_constraints[constraint_name]

    job_indexes = {
        index["name"]: (tuple(index["column_names"]), index["unique"])
        for index in inspector.get_indexes("transcription_jobs")
    }
    assert job_indexes == {
        "ix_transcription_jobs_lease_expires_at": (("lease_expires_at",), False),
        "ix_transcription_jobs_status": (("status",), False),
    }
    outbox_index_details = {
        index["name"]: index for index in inspector.get_indexes("outbox_events")
    }
    outbox_indexes = {
        index_name: (tuple(index["column_names"]), index["unique"])
        for index_name, index in outbox_index_details.items()
    }
    assert outbox_indexes == {
        "ix_outbox_events_job_uuid": (("job_uuid",), False),
        "ix_outbox_events_unpublished_created_at": (("created_at",), False),
    }
    partial_index = outbox_index_details["ix_outbox_events_unpublished_created_at"]
    assert "published_at IS NULL" in str(
        partial_index["dialect_options"]["postgresql_where"]
    )
    assert_migration_matches_metadata(database_engine, alembic_config)


def test_downgrade_base_from_0001_initial_schema(
    database_engine: Engine,
    alembic_config: Config,
) -> None:
    assert inspect(database_engine).get_table_names() == []
    run_alembic_command(database_engine, alembic_config, command.upgrade, "head")
    assert get_current_revision(database_engine) == REVISION_ID

    run_alembic_command(database_engine, alembic_config, command.downgrade, "base")

    inspector = inspect(database_engine)
    assert get_current_revision(database_engine) is None
    assert get_application_tables(database_engine) == set()
    assert {enum["name"] for enum in inspector.get_enums(schema="public")}.isdisjoint(
        {"transcription_job_status", "transcription_job_type"}
    )
    assert set(inspector.get_table_names()) <= {"alembic_version"}
    with database_engine.connect() as connection:
        version_count = connection.scalar(text("SELECT count(*) FROM alembic_version"))
    assert version_count == 0
