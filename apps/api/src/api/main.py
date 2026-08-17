from api.config import Config
from api.create_app import create_app_api


def main() -> None:
    app = create_app_api(Config)
    app.run(
        host=app.config["HOST"],
        port=app.config["API_PORT"],
        debug=app.config["DEBUG"],
    )


if __name__ == "__main__":
    main()
