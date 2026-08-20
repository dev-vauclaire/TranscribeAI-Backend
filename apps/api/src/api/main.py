import uvicorn

from api.config import ApiSettings
from api.create_app import create_app


def main() -> None:
    """Lance le serveur ASGI avec la configuration issue de l'environnement."""
    settings = ApiSettings()
    uvicorn.run(
        create_app(settings=settings),
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
