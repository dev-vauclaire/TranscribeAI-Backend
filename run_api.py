from app import create_app_api
from app.Config.APIConfig import APIConfig

# Lance l'app
if __name__ == "__main__":
    app = create_app_api(APIConfig)
    app.run(host=app.config["HOST"], port=app.config["API_PORT"], debug=app.config["DEBUG"])