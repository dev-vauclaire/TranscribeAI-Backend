import asyncio

import pytest
from sqlalchemy.exc import SQLAlchemyError

from api.Services.health import PostgresReadinessService


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class RecordingSession:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        wait_forever: bool = False,
    ) -> None:
        self.error = error
        self.wait_forever = wait_forever
        self.statements: list[str] = []

    async def __aenter__(self) -> "RecordingSession":
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: object,
    ) -> None:
        return None

    async def execute(self, statement: object) -> None:
        self.statements.append(str(statement))
        if self.error is not None:
            raise self.error
        if self.wait_forever:
            await asyncio.Event().wait()


class RecordingSessionFactory:
    def __init__(self, session: RecordingSession) -> None:
        self.session = session
        self.call_count = 0

    def __call__(self) -> RecordingSession:
        self.call_count += 1
        return self.session


async def test_postgres_readiness_executes_a_minimal_query() -> None:
    session = RecordingSession()
    factory = RecordingSessionFactory(session)
    service = PostgresReadinessService(
        factory,  # type: ignore[arg-type]
        timeout_seconds=1,
    )

    assert await service.is_ready() is True
    assert factory.call_count == 1
    assert session.statements == ["SELECT 1"]


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(SQLAlchemyError("database unavailable"), id="sqlalchemy"),
        pytest.param(OSError("connection refused"), id="network"),
    ],
)
async def test_postgres_readiness_reports_expected_connection_failures(
    error: Exception,
) -> None:
    service = PostgresReadinessService(
        RecordingSessionFactory(RecordingSession(error=error)),  # type: ignore[arg-type]
        timeout_seconds=1,
    )

    assert await service.is_ready() is False


async def test_postgres_readiness_is_bounded_by_its_timeout() -> None:
    service = PostgresReadinessService(
        RecordingSessionFactory(  # type: ignore[arg-type]
            RecordingSession(wait_forever=True)
        ),
        timeout_seconds=0.01,
    )

    assert await service.is_ready() is False
