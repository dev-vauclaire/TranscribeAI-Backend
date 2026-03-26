import os

# Gestion des fichiers audio
class AudioManager:
    # Initialisation avec le dossier de stockage des audios
    def __init__(self, folder_path : str):
        self.folder_path = folder_path
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)

    # Sauvegarde un fichier audio à l'emplacement spécifié
    def save_audio(self, file : str) -> str:
        file_path = os.path.join(self.folder_path, file.filename)
        file.save(file_path)
        return file_path

    # Supprime le fichier audio à l'emplacement spécifié
    def delete_audio(self, file_path : str)-> bool:
        if os.path.exists(file_path):
            os.remove(file_path)
            return True
        return False