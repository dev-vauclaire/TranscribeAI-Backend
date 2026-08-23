from fastapi import Response, status

from api.Schemas.health import HealthResponse
from api.Services.protocols import ReadinessService


async def get_liveness() -> HealthResponse:
    """Confirme que le processus ASGI peut encore traiter une requête."""
    return HealthResponse(status="alive")


async def get_readiness(
    *,
    response: Response,
    service: ReadinessService,
) -> HealthResponse:
    """Traduit la disponibilité PostgreSQL en contrat HTTP de readiness."""
    if await service.is_ready():
        return HealthResponse(status="ready")

    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status="not_ready")
