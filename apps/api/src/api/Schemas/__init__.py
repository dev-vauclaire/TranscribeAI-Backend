"""Schémas d'entrée et de sortie de l'API HTTP."""

from api.Schemas.health import HealthResponse
from api.Schemas.transcriptions import (
    TranscriptionCreatedResponse,
    TranscriptionCreationRequest,
    TranscriptionStatusResponse,
)

__all__ = [
    "HealthResponse",
    "TranscriptionCreatedResponse",
    "TranscriptionCreationRequest",
    "TranscriptionStatusResponse",
]
