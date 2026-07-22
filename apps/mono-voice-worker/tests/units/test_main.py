from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import mono_voice_worker.main as main_module


pytestmark = pytest.mark.unit


@pytest.fixture
def bootstrap_dependencies(monkeypatch):
    settings = SimpleNamespace(
        pg_dsn="postgresql+psycopg2://user:password@database/transcribe",
        redis_dsn="redis://redis:6379/0",
        redis_queue_name_mono_voice="mono-jobs",
        whisper_service_url="http://whisper:5002",
    )
    engine = Mock()
    session_factory = Mock()
    redis_queue_service = Mock()
    whisper_client = Mock()
    worker = Mock()

    monkeypatch.setattr(
        main_module,
        "WorkerMonoVoiceSettings",
        Mock(return_value=settings),
    )
    create_db_engine = Mock(return_value=engine)
    monkeypatch.setattr(main_module, "create_db_engine", create_db_engine)
    create_session_factory = Mock(return_value=session_factory)
    monkeypatch.setattr(
        main_module,
        "create_session_factory",
        create_session_factory,
    )
    redis_service_class = Mock(return_value=redis_queue_service)
    monkeypatch.setattr(main_module, "RedisQueueService", redis_service_class)
    whisper_client_class = Mock(return_value=whisper_client)
    monkeypatch.setattr(main_module, "ClientWhisper", whisper_client_class)
    worker_class = Mock(return_value=worker)
    monkeypatch.setattr(main_module, "WorkerMonoVoice", worker_class)

    return SimpleNamespace(
        create_db_engine=create_db_engine,
        create_session_factory=create_session_factory,
        engine=engine,
        redis_queue_service=redis_queue_service,
        redis_service_class=redis_service_class,
        session_factory=session_factory,
        settings=settings,
        whisper_client=whisper_client,
        whisper_client_class=whisper_client_class,
        worker=worker,
        worker_class=worker_class,
    )


def test_main_checks_dependencies_before_starting_worker(
    monkeypatch,
    bootstrap_dependencies,
):
    check_postgres_connection = Mock()
    monkeypatch.setattr(
        main_module,
        "check_postgres_connection",
        check_postgres_connection,
    )
    bootstrap_dependencies.worker.run_once.side_effect = KeyboardInterrupt

    main_module.main()

    check_postgres_connection.assert_called_once_with(
        bootstrap_dependencies.session_factory
    )
    bootstrap_dependencies.redis_queue_service.check_redis_connection.assert_called_once_with()
    bootstrap_dependencies.whisper_client.check_whisper_connection.assert_called_once_with()
    bootstrap_dependencies.worker_class.assert_called_once_with(
        settings=bootstrap_dependencies.settings,
        session_factory=bootstrap_dependencies.session_factory,
        redis_queue_service=bootstrap_dependencies.redis_queue_service,
        client_whisper=bootstrap_dependencies.whisper_client,
    )
    bootstrap_dependencies.worker.run_once.assert_called_once_with()
    bootstrap_dependencies.engine.dispose.assert_called_once_with()


def test_main_does_not_start_worker_when_healthcheck_fails(
    monkeypatch,
    bootstrap_dependencies,
):
    monkeypatch.setattr(
        main_module,
        "check_postgres_connection",
        Mock(side_effect=ConnectionError("database unavailable")),
    )

    main_module.main()

    bootstrap_dependencies.redis_queue_service.check_redis_connection.assert_not_called()
    bootstrap_dependencies.whisper_client.check_whisper_connection.assert_not_called()
    bootstrap_dependencies.worker_class.assert_not_called()
    bootstrap_dependencies.engine.dispose.assert_called_once_with()
