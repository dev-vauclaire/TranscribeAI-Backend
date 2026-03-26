from flask import request
import app.Helpers as Helpers
from flask import current_app
from app.Schemas import TranscriptionBatchSchema

# Créer un job et retourne son uuid
def createBatchJob():
    # Validation des données reçues
    is_valid, message = TranscriptionBatchSchema.check_params(request.form)
    
    if not is_valid:
        return Helpers.error(message=message, status_code=400)
    
    batch_settings = message
    
    # Récupère les services qui nous interesse 
    audio_manager = current_app.extensions['audio_manager']
    job_service = current_app.extensions['job_service']
    redis_queue_service = current_app.extensions['redis_batch_queue_service']

    # Générer un identifiant unique pour le job
    job_id = Helpers.generate_token()

    # Enregistrer le fichier audio reçu dans un chemin temporaire
    audio_file = request.files['audioFile']
    audio_file.filename = job_id
    file_path = audio_manager.save_audio(audio_file)

    # Créer une entrée job dans la base de données avec le statut "en attente"
    job_service.create_job(job_id, file_path, "BATCH", "PENDING", batch_settings)

    # Enqueue le job dans redis
    redis_queue_service.enqueue_job(job_id)

    return Helpers.success({"job_id": job_id, "message" : "Votre demande de transcription est dans la file d'attente"}, 200)

# Récupérer la transcription par UUID
def getBatchTranscriptionByUuid():

    # Récupérer l'UUID du job depuis les paramètres de la requête
    job_id = request.args.get('job_id')
    if not job_id:
        return Helpers.error("Il manque le paramètre job_id", 400)

    # Contacter le service de gestion des jobs pour obtenir le statut et la transcription
    job_service = current_app.extensions['job_service']
    job = job_service.get_job_by_uuid(job_id)

    if not job or job.type != "BATCH":
        return Helpers.error("Votre demande de transcription n'existe pas", 404)
    elif job.status == "PENDING":
        redis_queue_service = current_app.extensions['redis_batch_queue_service']
        position = redis_queue_service.get_queue_position(job.id)
        return Helpers.success({"job_id": job.id,"status": job.status, "position": position})
    elif job.status == "PROCESSING":
        return Helpers.success({"job_id": job.id,"status": job.status,})
    elif job.status == "FAILED":
        job_service.delete_job(job.id)
        return Helpers.error("La transcription a échouée", 500)
    else:
        # Calcul du temps de la transcription en seconde 
        transcription_duration = 0
        if job.end_at and job.created_at:
            transcription_duration = (job.end_at - job.created_at).total_seconds()
        # Supprimer le job après récupération à voir comment gérer ça plus tard
        job_service.delete_job(job.id)
        return Helpers.success({
            "job_id": job.id,
            "status": job.status,
            "result": job.result,
            "transcription_time": transcription_duration
        }, 200)