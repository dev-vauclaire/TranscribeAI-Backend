from flask import Flask
from dotenv import load_dotenv
from flask_cors import CORS

# Charger les variables d'environnement depuis le fichier .env
load_dotenv()

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import OperationalError
db = SQLAlchemy()

# fonction commune à toutes les App flask
def create_app(config_class):
    app = Flask(__name__)

    # Charger la configuration
    app.config.from_object(config_class)

    # Initialisation de la base de données
    db.init_app(app)

    from app.Services.AudioManager import AudioManager
    from app.Services.JobService import JobService

    # Initialisation du service de gestion des jobs
    app.extensions['job_service'] = JobService(db)
    # Initialisation du service de gestion des fichiers audio
    app.extensions['audio_manager'] = AudioManager(app.config['AUDIO_STORAGE_PATH'])

    return app

# Configuration spécifique au worker batch
def create_app_worker_batch(config_class):

    app = create_app(config_class)
    
    # Configuration spécifique aux workers peut être ajoutée ici
    from app.Services.TranscriptionService import TranscriptionService
    app.extensions['transcription_service'] = TranscriptionService(app.config['WHISPER_SERVICE_URL'])
    from app.Services.RedisQueueService import RedisQueueService
    app.extensions['redis_batch_queue_service'] = RedisQueueService(app.config['REDIS_URL'], "batch_job_queue")

    return app

# Configuration spécifique au worker de diarization
def create_app_worker_diarization(config_class):

    app = create_app(config_class)

    from app.Services.DiarizationService import DiarizationService
    app.extensions['diarization_service'] = DiarizationService(app.config['WHISPERX_SERVICE_URL'])
    from app.Services.RedisQueueService import RedisQueueService
    app.extensions['redis_diarization_queue_service'] = RedisQueueService(app.config['REDIS_URL'], "diarization_job_queue")

    return app

def create_app_api(config_class):

    app = create_app(config_class)

    import time
    # On tente de créer la table job avec une boucle de reconnexion
    with app.app_context():
        retries = 5
        while retries > 0:
            try:
                db.create_all()
                print("Base de données initialisée avec succès.")
                break
            except OperationalError:
                retries -= 1
                print(f"Postgres n'est pas prêt... Nouvelle tentative dans 2s ({retries} essais restants)")
                time.sleep(2)

        if retries <= 0:
            print("Erreur : Impossible de se connecter à Postgres après plusieurs tentatives.")

    from app.Services.RedisQueueService import RedisQueueService
    app.extensions['redis_batch_queue_service'] = RedisQueueService(app.config['REDIS_URL'], "batch_job_queue")
    app.extensions['redis_diarization_queue_service'] = RedisQueueService(app.config['REDIS_URL'], "diarization_job_queue")
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Load de toutes les routes
    from app.Routes import register_routes
    register_routes(app)

    return app

