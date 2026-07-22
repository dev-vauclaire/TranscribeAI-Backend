from pathlib import Path
from typing import BinaryIO


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
    def __init__(self, folder_path: str):
        self.folder_path = folder_path
        self._folder = Path(folder_path).resolve()
        self._folder.mkdir(parents=True, exist_ok=True)

    def _resolve_audio_path(self, filename: str) -> Path:
        """Resolve a storage filename without allowing access outside the audio folder."""
        file_path = (self._folder / filename).resolve()
        if not file_path.is_relative_to(self._folder):
            raise WrongAudioPathError(
                "Tentative d'accès en dehors du dossier audio autorisé."
            )
        return file_path

    # Sauvegarde un fichier audio à l'emplacement spécifié
    def save_audio(self, file: UploadedAudio) -> str:

        try:
            file_path = self._resolve_audio_path(file.filename)
        except WrongAudioPathError as error:
            raise WrongAudioPathError(
                "Tentative de sauvegarde en dehors du dossier audio autorisé."
            ) from error

        with file_path.open("wb") as f:
            f.write(file.content)
        return file.filename

    # Supprime le fichier audio à l'emplacement spécifié
    def delete_audio(self, filename: str) -> bool:
        try:
            file_path = self._resolve_audio_path(filename)
        except WrongAudioPathError as error:
            raise WrongAudioPathError(
                "Tentative de suppression en dehors du dossier audio autorisé."
            ) from error

        if file_path.is_file():
            file_path.unlink()
            return True
        return False

    # Ouvre le fichier audio à l'emplacement spécifié pour lecture binaire
    def open_audio(self, filename: str) -> BinaryIO:
        file_path = self._resolve_audio_path(filename)
        return file_path.open("rb")
