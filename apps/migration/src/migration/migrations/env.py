from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine, pool

from transcribe_ai_shared.database import Base, DatabaseSettings


config = context.config

if config.config_file_name is not None and config.attributes.get(
    "configure_logger", True
):
    # Les migrations sont aussi invoquées dans le processus pytest par les
    # intégrations applicatives : ne pas désactiver leurs loggers existants.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def get_database_url() -> str:
    configured_url = config.attributes.get("database_url")
    if configured_url is not None:
        return str(configured_url)
    return str(DatabaseSettings(_env_file=None).url)


def configure_context(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_server_default=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        compare_server_default=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
        literal_binds=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    provided_connection = config.attributes.get("connection")
    if provided_connection is not None:
        configure_context(provided_connection)
        return

    engine = create_engine(
        get_database_url(),
        hide_parameters=True,
        poolclass=pool.NullPool,
        pool_pre_ping=True,
    )
    try:
        with engine.connect() as connection:
            configure_context(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
