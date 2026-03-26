from flask import request
from functools import wraps
from app.Helpers.responses import error
from app.Config.APIConfig import APIConfig

"""
Décorateur qui vérifie l'authenticité d'un fichier audio : 
- Existance 
- Nom non vide
- Mimetype du binaire
- Taille maximale
"""

def check_audio(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        print("Vérification du fichier audio...")
        file = request.files.get('audioFile')
        
        # 1. Présence et Nom
        if not file or file.filename == '':
            return error("Fichier audio manquant ou invalide", 400)

        # 2. Type de contenu
        if not file.mimetype.startswith("audio/"):
            return error(f"Format {file.mimetype} non supporté", 400)
        
        # 3. Validation Taille (encapsulée)
        if not _is_size_valid(file, APIConfig.MAX_AUDIO_SIZE_MB):
            return error(f"Fichier trop lourd (Max: {APIConfig.MAX_AUDIO_SIZE_MB}MB)", 400)

        return f(*args, **kwargs)
    return wrapper

def _is_size_valid(file, max_mb):
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    return size <= (max_mb * 1024 * 1024)
