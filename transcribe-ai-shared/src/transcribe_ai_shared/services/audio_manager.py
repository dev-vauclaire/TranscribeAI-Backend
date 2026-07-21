import os

class WrongAudioPathError(Exception):
    """Exception levée lorsqu'une tentative de sauvegarde d'un fichier audio est faite en dehors du dossier autorisé."""
    pass

class UploadedAudio:
    """Représente un fichier audio téléchargé avec un nom de fichier et un contenu binaire."""
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.content = content

# Gestion des fichiers audio
class AudioManager:
    # Initialisation avec le dossier de stockage des audios
    def __init__(self, folder_path : str):
        self.folder_path = folder_path
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)

    # Sauvegarde un fichier audio à l'emplacement spécifié
    def save_audio(self, file : UploadedAudio) -> str:

        # Vérifie que le fichier ne tente pas d'échapper au dossier audio
        if ".." in file.filename or file.filename.startswith("/"):
            raise WrongAudioPathError("Tentative de sauvegarde en dehors du dossier audio autorisé.")

        file_path = os.path.join(self.folder_path, file.filename)
        with open(file_path, "wb") as f:
            f.write(file.content)
        return file_path

    # Supprime le fichier audio à l'emplacement spécifié
    def delete_audio(self, file : UploadedAudio) -> bool:
        # Vérifie que le fichier ne tente pas d'échapper au dossier audio
        file_path = os.path.join(self.folder_path, file.filename)
        
        if ".." in file.filename or file.filename.startswith("/"):
            raise WrongAudioPathError("Tentative de suppression en dehors du dossier audio autorisé.")

        if os.path.exists(file_path) and file_path.startswith(self.folder_path):
            os.remove(file_path)
            return True
        return False