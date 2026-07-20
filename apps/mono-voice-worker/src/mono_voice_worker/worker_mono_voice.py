import time
from mono_voice_worker import WorkerMonoVoiceConfig
from mono_voice_worker import ClientWhisper
from transcribe_ai_shared import RedisQueueService
from transcribe_ai_shared import JobRepositorie
from transcribe_ai_shared import AudioManager

class WorkerMonoVoice:

    def __init__(self, config : WorkerMonoVoiceConfig):
        self.config = config
        self.redis_queue_service = RedisQueueService(config.REDIS_URL, config.REDIS_QUEUE_NAME_MONO_VOICE)

        # TODO : Initialiser la session (je pense qu'on va juste appler session factory depuis transcribe_ai_shared)
        session = None
        self.job_repositorie = JobRepositorie(session)
        
        self.audio_manager = AudioManager(config.AUDIO_FOLDER_PATH)
        self.whisper_client = ClientWhisper(config.WHISPER_SERVICE_URL)

    def run_once(self):
        # Récupérer un job de la file d'attente Redis (bloquant)
        job_id = self.redis_queue_service.pop_job_blocking()
        print(f"Traitement du job {job_id}...")
        
        # Mettre à jour le statut du job en "IN_PROGRESS"
        self.job_repositorie.update_status(job_id, "PROCESSING")
        
        try:
            # Récupérer le chemin du fichier audio depuis la base de données
            job = self.job_repositorie.get_job_by_id(job_id)
            audio_file_path = job.file_path
            time.sleep(5)
            with open(audio_file_path, 'rb') as f:
                audio_file = f
                # Envoyer le fichier audio au service Whisper pour transcription
                transcription = self.whisper_client.send_to_whisper_service(audio_file)
            
            # Mettre à jour le job avec la transcription et le statut "COMPLETED"
            self.job_repositorie.complete_job(job_id, transcription)
        
        except Exception as e:
            # En cas d'erreur, mettre à jour le statut du job en "FAILED"
            self.job_repositorie.fail_job(job_id)
            print(f"Erreur lors du traitement du job {job_id}: {e}")

        finally:
            # Supprimer le fichier audio après traitement
            if 'audio_file_path' in locals():
                self.audio_manager.delete_audio(audio_file_path)

        # Petite pause pour éviter une boucle trop rapide
        time.sleep(self.config.WORKER_LOOP_SLEEP_TIME)