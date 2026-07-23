import logging
import time
from mono_voice_worker import ClientWhisper, WorkerMonoVoice, WorkerMonoVoiceSettings
from transcribe_ai_shared import (
    DatabaseConfig,
    RedisQueueService,
    AudioManager,
    check_postgres_connection,
    create_db_engine,
    create_session_factory,
)


def main() -> None:
    mono_voice_settings = WorkerMonoVoiceSettings()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    engine = create_db_engine(DatabaseConfig(url=str(mono_voice_settings.pg_dsn)))

    try:
        session_factory = create_session_factory(engine)
        redis_queue_service = RedisQueueService(
            str(mono_voice_settings.redis_dsn),
            mono_voice_settings.redis_queue_name_mono_voice,
        )
        client_whisper = ClientWhisper(mono_voice_settings.whisper_service_url)
        audio_manager = AudioManager(mono_voice_settings.audio_folder_path)

        logging.info("Lancement des tests de connexion aux services...")
        check_postgres_connection(session_factory)
        logging.info("Connexion à la BDD validée !")
        redis_queue_service.check_redis_connection()
        logging.info("Connexion à Redis validée !")
        client_whisper.check_whisper_connection()
        logging.info("Connexion au service Whisper validée !")

        worker = WorkerMonoVoice(
            session_factory=session_factory,
            redis_queue_service=redis_queue_service,
            client_whisper=client_whisper,
            audio_manager=audio_manager,
        )

        logging.info("🚀 Worker démarré")
        logging.info(f"Configuration: {worker}")

        while True:
            worker_payload = worker.run_once()
            logging.info(f"Worker payload: {worker_payload}")
            time.sleep(mono_voice_settings.worker_loop_sleep_time)
    except KeyboardInterrupt:
        logging.info("Arrêt du worker demandé par l'utilisateur.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
