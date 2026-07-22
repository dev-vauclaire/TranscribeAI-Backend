from mono_voice_worker import ClientWhisper, WorkerMonoVoice, WorkerMonoVoiceSettings
from transcribe_ai_shared import (
    DatabaseConfig,
    RedisQueueService,
    check_postgres_connection,
    create_db_engine,
    create_session_factory,
)


def main() -> None:
    mono_voice_settings = WorkerMonoVoiceSettings()
    engine = create_db_engine(DatabaseConfig(url=str(mono_voice_settings.pg_dsn)))

    try:
        session_factory = create_session_factory(engine)
        redis_queue_service = RedisQueueService(
            str(mono_voice_settings.redis_dsn),
            mono_voice_settings.redis_queue_name_mono_voice,
        )
        client_whisper = ClientWhisper(mono_voice_settings.whisper_service_url)

        print("Lancement des tests de connexion aux services...")
        check_postgres_connection(session_factory)
        print("Connexion à la BDD validée !")
        redis_queue_service.check_redis_connection()
        print("Connexion à Redis validée !")
        client_whisper.check_whisper_connection()
        print("Connexion au service Whisper validée !")

        worker = WorkerMonoVoice(
            settings=mono_voice_settings,
            session_factory=session_factory,
            redis_queue_service=redis_queue_service,
            client_whisper=client_whisper,
        )

        print("🚀 Worker démarré")
        print(f"Configuration: {worker}")

        while True:
            worker.run_once()
    except KeyboardInterrupt:
        print("Arrêt du worker demandé par l'utilisateur.")
    except Exception as e:
        print(e)
    finally:
        engine.dispose()
        print("Worker arrêté proprement.")


if __name__ == "__main__":
    main()
