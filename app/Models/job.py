from sqlalchemy.sql import func
from sqlalchemy import Enum
from sqlalchemy.dialects.postgresql import JSONB  # Import pour PostgreSQL
from app import db

class Job(db.Model):
    __tablename__ = "job"
    id = db.Column(db.String(255), primary_key=True)

    type = db.Column(
        Enum("BATCH", "DIARIZATION", name="job_type"),
        nullable=False
    )
    
    status = db.Column(
        Enum("PENDING", "PROCESSING", "COMPLETED", "FAILED", name="job_status"),
        nullable=False,
        server_default="PENDING"
    )
    settings = db.Column(JSONB, nullable=True)
    file_path = db.Column(db.String(255), nullable=False)
    result = db.Column(JSONB, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    end_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "result": self.result,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "end_at": self.end_at.isoformat() if self.end_at else None
        }