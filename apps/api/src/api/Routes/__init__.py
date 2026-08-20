"""Routes FastAPI de l'application."""

from fastapi import APIRouter

from api.Routes.transcriptions import router as transcription_router


api_router = APIRouter(prefix="/api")
api_router.include_router(transcription_router)

__all__ = ["api_router"]
