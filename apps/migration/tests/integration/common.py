"""Helpers partagés par les tests versionnés des migrations Alembic."""

from collections.abc import Callable

from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect


def run_alembic_command(
    engine: Engine,
    config: Config,
    operation: Callable[[Config, str], None],
    target: str,
) -> None:
    """Exécute une commande Alembic sur la connexion du test."""
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        try:
            operation(config, target)
        finally:
            config.attributes.pop("connection", None)


def get_current_revision(engine: Engine) -> str | None:
    """Retourne la révision actuellement enregistrée par Alembic."""
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def get_application_tables(engine: Engine) -> set[str]:
    """Retourne les tables métier sans la table technique Alembic."""
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


def normalize_sql_expression(expression: str) -> str:
    """Normalise une expression SQL introspectée pour des assertions stables."""
    return " ".join(expression.lower().split())
