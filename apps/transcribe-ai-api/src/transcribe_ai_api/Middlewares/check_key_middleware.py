from flask import request, current_app
from apps.api.src.Helpers.responses import success, error

def check_key_middleware():
    if request.method == "OPTIONS":
        return success()
    
    api_key_sent = request.headers.get("X-API-KEY")
    api_key = current_app.config.get("SECRET_KEY")
    if api_key_sent != api_key:
        # 🛑 Avortement du pipeline → aucun controller n'est exécuté
        return error("error: Utilisateur non authorisé", 401)
