import time
from app import create_app_worker_batch
from app.Config.WorkerBatchConfig import WorkerBatchConfig

def worker_loop():
    while True:
        redis_queue_service = app.extensions['redis_batch_queue_service']
        job_service = app.extensions['job_service']
        audio_manager = app.extensions['audio_manager']
        whisper_batch_service = app.extensions['transcription_service']

        # Récupérer un job de la file d'attente Redis (bloquant)
        job_id = redis_queue_service.pop_job_blocking()
        print(f"Traitement du job {job_id}...")
        
        # Mettre à jour le statut du job en "IN_PROGRESS"
        job_service.update_status(job_id, "PROCESSING")
        
        try:
            # Récupérer le chemin du fichier audio depuis la base de données
            job = job_service.get_job_by_id(job_id)
            audio_file_path = job.file_path
            time.sleep(5)
            with open(audio_file_path, 'rb') as f:
                audio_file = f
                # Envoyer le fichier audio au service Whisper pour transcription
                transcription = whisper_batch_service.send_to_whisper_service(audio_file)
            
            # Mettre à jour le job avec la transcription et le statut "COMPLETED"
            job_service.complete_job(job_id, transcription)
        
        except Exception as e:
            # En cas d'erreur, mettre à jour le statut du job en "FAILED"
            job_service.fail_job(job_id)
            print(f"Erreur lors du traitement du job {job_id}: {e}")

        finally:
            # Supprimer le fichier audio après traitement
            if 'audio_file_path' in locals():
                audio_manager.delete_audio(audio_file_path)

        # Petite pause pour éviter une boucle trop rapide
        time.sleep(app.config.get("WORKER_LOOP_SLEEP_TIME", 1))

if __name__ == "__main__":
    # 1. On crée l'application Flask avec la configuration du worker
    app = create_app_worker_batch(WorkerBatchConfig)

    # 2. On entre dans le contexte de l'application Flask
    # Cela permet d'accéder à current_app.config, à la BDD, et charge les variables d'env
    with app.app_context():
        print("🚀 Worker démarré avec le contexte de l'application Flask.")
        print(f"📂 Dossier Audio configuré : {app.config.get('AUDIO_STORAGE_PATH')}")
        worker_loop()
