import logging

import uvicorn

from api.config import ApiSettings
from api.create_app import create_app
from transcribe_ai_shared.observability import configure_logging, log_event


LOGGER = logging.getLogger(__name__)
SERVICE = "api"


def main() -> int:
    """Lance le serveur ASGI avec la configuration issue de l'environnement."""
    configure_logging(service=SERVICE)
    try:
        settings = ApiSettings()
        uvicorn.run(
            create_app(settings=settings),
            host=settings.host,
            log_config=None,
            port=settings.port,
        )
    except KeyboardInterrupt:
        log_event(
            LOGGER,
            logging.WARNING,
            service=SERVICE,
            event="api_process",
            action="interrupted",
        )
        return 130
    except Exception as error:
        log_event(
            LOGGER,
            logging.ERROR,
            service=SERVICE,
            event="api_process",
            action="failed",
            error_type=type(error).__name__,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
