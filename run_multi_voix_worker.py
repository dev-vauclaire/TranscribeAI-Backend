import time
from app import create_app_worker_diarization
from app.Config.WorkerDiarizationConfig import WorkerDiarizationConfig

def worker_loop():
    while True:
        redis_queue_service = app.extensions['redis_diarization_queue_service']
        job_service = app.extensions['job_service']
        audio_manager = app.extensions['audio_manager']
        whisperx_diarize_service = app.extensions['diarization_service']

        # Récupérer un job de la file d'attente Redis (bloquant)
        job_id = redis_queue_service.pop_job_blocking()
        print(f"Traitement du job {job_id}...")
        
        # Mettre à jour le statut du job en "IN_PROGRESS"
        job_service.update_status(job_id, "PROCESSING")
        
        try:
            # Récupérer le job depuis la base de données
            job = job_service.get_job_by_id(job_id)

            max_speakers = job.settings.get('max_speakers', None)
            min_speakers = job.settings.get('min_speakers', None)

            params = {
                "min_speakers": min_speakers,
                "max_speakers": max_speakers
            }

            audio_file_path = job.file_path
            with open(audio_file_path, 'rb') as f:
                audio_file = f
                # Envoyer le fichier audio au service Whisperx pour diarization
                diarization = whisperx_diarize_service.send_to_whisperx_service(audio_file, params)
                
            # Mettre à jour le job avec la diarization et le statut "COMPLETED"
            job_service.complete_job(job_id, diarization)
        
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
    app = create_app_worker_diarization(WorkerDiarizationConfig)

    # 2. On entre dans le contexte de l'application Flask
    # Cela permet d'accéder à current_app.config, à la BDD, et charge les variables d'env
    with app.app_context():
        print("🚀 Worker diarization demarré ")
        print(f"📂 Dossier Audio configuré : {app.config.get('AUDIO_STORAGE_PATH')}")
        worker_loop()
