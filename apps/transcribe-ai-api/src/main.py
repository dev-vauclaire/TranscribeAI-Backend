from app import create_app_api
from transcribe_ai_api.config import Config
from common_packages import create_app
from transcribe_ai_api.Routes import register_routes
from common_packages.Services.RedisQueueService import RedisQueueService

db = SQLAlchemy()

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

    app.extensions['redis_batch_queue_service'] = RedisQueueService(app.config['REDIS_URL'], "batch_job_queue")
    app.extensions['redis_diarization_queue_service'] = RedisQueueService(app.config['REDIS_URL'], "diarization_job_queue")
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Load de toutes les routes
    register_routes(app)

    return app

# Lance l'app
if __name__ == "__main__":
    app = create_app_api(Config)
    app.run(host=app.config["HOST"], port=app.config["API_PORT"], debug=app.config["DEBUG"])