import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Boolean, DateTime, Engine, inspect, text
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID

from .common import (
    get_application_tables,
    get_current_revision,
    normalize_sql_expression,
    run_alembic_command,
)


pytestmark = pytest.mark.integration

REVISION_ID = "0001_initial_schema"
APPLICATION_TABLES = {
    "transcription_jobs",
    "transcription_results",
}


def test_upgrade_0001_initial_schema_from_base(
    database_engine: Engine,
    alembic_config: Config,
) -> None:
    assert inspect(database_engine).get_table_names() == []
    assert get_current_revision(database_engine) is None

    run_alembic_command(
        database_engine,
        alembic_config,
        command.upgrade,
        REVISION_ID,
    )

    inspector = inspect(database_engine)
    assert get_current_revision(database_engine) == REVISION_ID
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
    assert isinstance(job_columns["dispatch_required"]["type"], Boolean)
    assert job_columns["dispatch_required"]["nullable"] is False
    assert (
        normalize_sql_expression(job_columns["dispatch_required"]["default"]) == "true"
    )
    assert isinstance(job_columns["last_dispatched_at"]["type"], DateTime)
    assert job_columns["last_dispatched_at"]["type"].timezone is True
    assert job_columns["last_dispatched_at"]["nullable"] is True
    assert job_columns["last_dispatched_at"]["default"] is None
    assert isinstance(result_columns["result"]["type"], JSONB)
    assert result_columns["note"]["nullable"] is True

    assert inspector.get_pk_constraint("transcription_jobs")["constrained_columns"] == [
        "job_uuid"
    ]
    assert inspector.get_pk_constraint("transcription_results")[
        "constrained_columns"
    ] == ["job_uuid"]

    foreign_keys = inspector.get_foreign_keys("transcription_results")
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
        index["name"]: index for index in inspector.get_indexes("transcription_jobs")
    }
    assert job_indexes.keys() == {"idx_job_dispatch", "idx_job_expired_lease"}

    dispatch_index = job_indexes["idx_job_dispatch"]
    assert dispatch_index["column_names"] == ["created_at"]
    assert dispatch_index["unique"] is False
    dispatch_predicate = normalize_sql_expression(
        str(dispatch_index["dialect_options"]["postgresql_where"])
    )
    assert "status = 'queued'::transcription_job_status" in dispatch_predicate
    assert "dispatch_required is true" in dispatch_predicate

    expired_lease_index = job_indexes["idx_job_expired_lease"]
    assert expired_lease_index["column_names"] == ["lease_expires_at"]
    assert expired_lease_index["unique"] is False
    expired_lease_predicate = normalize_sql_expression(
        str(expired_lease_index["dialect_options"]["postgresql_where"])
    )
    assert "status = 'processing'::transcription_job_status" in expired_lease_predicate


def test_downgrade_base_from_0001_initial_schema(
    database_engine: Engine,
    alembic_config: Config,
) -> None:
    assert inspect(database_engine).get_table_names() == []
    run_alembic_command(
        database_engine,
        alembic_config,
        command.upgrade,
        REVISION_ID,
    )
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
