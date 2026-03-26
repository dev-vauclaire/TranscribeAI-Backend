from app.Models.job import Job as JobModel
from datetime import datetime, timezone

class JobService:
    def __init__(self, db):
        self.db = db

    # Créer un job en spécifiant son type, son uuid, le chemin où il est stocké
    def create_job(self, _id : str, _file_path : str, _type : str, _status : str, _settings: dict = None) -> JobModel:
        job = JobModel(
            id=_id,
            file_path=_file_path,
            type=_type,
            status=_status,
            settings=_settings
        )
        self.db.session.add(job)
        self.db.session.commit()
        return job

    # Change le status d'un job en particulier
    def update_status(self, job_id, status: str) -> JobModel:
        job = JobModel.query.get(job_id)
        if not job:
            return None
        job.status = status
        self.db.session.commit()
        return job

    # Finalise le job en stockant le résultat (JSON) quelle que soit sa nature.
    def complete_job(self, job_id, result_data: dict):
        job = JobModel.query.get(job_id)
        if not job:
            return None
        job.status = "COMPLETED"
        job.result = result_data
        job.end_at = datetime.now(timezone.utc)
        self.db.session.commit()
        return job
    
    def get_job_by_id(self, job_id):
        job = JobModel.query.get(job_id)
        return job
    
    # Supprime un job une fois qu'il est completed
    def delete_job(self, job_id):
        job = JobModel.query.get(job_id)
        if not job:
            return False
        if job.status in ["COMPLETED", "FAILED"]:
            self.db.session.delete(job)
            self.db.session.commit()
            return True
        return False

    # Marque le job comme failed en ajoutant un message d'erreur
    def fail_job(self, job_id, error_msg: str = "Une erreur est survenue"):
        job = JobModel.query.get(job_id)
        if not job:
            return None
        job.status = "FAILED"
        job.error_message = error_msg
        self.db.session.commit()
        return job