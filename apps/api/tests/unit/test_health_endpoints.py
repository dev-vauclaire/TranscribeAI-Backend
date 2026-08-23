from decimal import Decimal

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
import pytest

from api.config import ApiSettings
from api.create_app import create_app


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

TEST_SETTINGS = ApiSettings(
    host="127.0.0.1",
    port=8000,
    max_upload_size_bytes=100,
    ffprobe_path="ffprobe",
    ffprobe_timeout_seconds=30,
    readiness_timeout_seconds=1,
    fast_max_duration_seconds=Decimal("900"),
    long_form_diarization_max_duration_seconds=Decimal("14400"),
)


class RecordingReadinessService:
    """Double qui rend visible tout accès aux dépendances de readiness."""

    def __init__(self, *, ready: bool) -> None:
        self.ready = ready
        self.call_count = 0

    async def is_ready(self) -> bool:
        self.call_count += 1
        return self.ready


def build_app(service: RecordingReadinessService) -> FastAPI:
    return create_app(
        settings=TEST_SETTINGS,
        transcription_service=object(),  # type: ignore[arg-type]
        transcription_query_service=object(),  # type: ignore[arg-type]
        readiness_service=service,
    )


async def get_health(app: FastAPI, path: str) -> Response:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.get(path)


async def test_live_returns_200_without_checking_readiness_dependencies() -> None:
    readiness_service = RecordingReadinessService(ready=False)

    response = await get_health(build_app(readiness_service), "/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
    assert readiness_service.call_count == 0


@pytest.mark.parametrize(
    ("ready", "expected_status", "expected_payload"),
    [
        pytest.param(True, 200, {"status": "ready"}, id="postgres-ready"),
        pytest.param(
            False,
            503,
            {"status": "not_ready"},
            id="postgres-unavailable",
        ),
    ],
)
async def test_ready_translates_the_postgres_readiness_state(
    ready: bool,
    expected_status: int,
    expected_payload: dict[str, str],
) -> None:
    readiness_service = RecordingReadinessService(ready=ready)

    response = await get_health(build_app(readiness_service), "/health/ready")

    assert response.status_code == expected_status
    assert response.json() == expected_payload
    assert readiness_service.call_count == 1


async def test_health_routes_are_not_exposed_under_the_api_prefix() -> None:
    app = build_app(RecordingReadinessService(ready=True))

    live_response = await get_health(app, "/api/health/live")
    ready_response = await get_health(app, "/api/health/ready")

    assert live_response.status_code == 404
    assert ready_response.status_code == 404


async def test_ready_documents_its_unavailable_response_schema() -> None:
    app = build_app(RecordingReadinessService(ready=True))

    unavailable_response = app.openapi()["paths"]["/health/ready"]["get"]["responses"][
        "503"
    ]

    assert unavailable_response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HealthResponse"
    }
