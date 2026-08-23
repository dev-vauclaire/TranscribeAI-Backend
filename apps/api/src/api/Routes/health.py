from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from api.Controllers import get_liveness, get_readiness
from api.Routes.dependencies import get_readiness_service
from api.Schemas import HealthResponse
from api.Services.protocols import ReadinessService


router = APIRouter(prefix="/health", tags=["health"])


@router.get(
    "/live",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
)
async def health_live() -> HealthResponse:
    """Expose une liveness indépendante des services externes."""
    return await get_liveness()


@router.get(
    "/ready",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "PostgreSQL n'est pas disponible dans le délai configuré.",
            "model": HealthResponse,
        }
    },
)
async def health_ready(
    response: Response,
    service: Annotated[ReadinessService, Depends(get_readiness_service)],
) -> HealthResponse:
    """Expose la disponibilité des dépendances indispensables à l'API."""
    return await get_readiness(response=response, service=service)
