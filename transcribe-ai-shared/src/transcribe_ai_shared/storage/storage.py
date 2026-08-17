from pathlib import Path
from typing import BinaryIO

from transcribe_ai_shared.storage.exceptions import WrongAudioPathError


class UploadedAudio:
    """Fichier audio reçu avec son nom relatif et son contenu binaire."""

    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.content = content


class AudioStorageService:
    """Stocke les fichiers audio dans un dossier local confiné."""

    def __init__(self, audio_storage_path: str | Path):
        self.audio_storage_path = Path(audio_storage_path)
        self._folder = self.audio_storage_path.resolve()
        self._folder.mkdir(parents=True, exist_ok=True)

    def _resolve_audio_path(self, filename: str) -> Path:
        file_path = (self._folder / filename).resolve()
        if not file_path.is_relative_to(self._folder):
            raise WrongAudioPathError(
                "Tentative d'accès en dehors du dossier audio autorisé."
            )
        return file_path

    def save_audio(self, file: UploadedAudio) -> str:
        try:
            file_path = self._resolve_audio_path(file.filename)
        except WrongAudioPathError as error:
            raise WrongAudioPathError(
                "Tentative de sauvegarde en dehors du dossier audio autorisé."
            ) from error

        with file_path.open("wb") as audio_file:
            audio_file.write(file.content)
        return file.filename

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

    def open_audio(self, filename: str) -> BinaryIO:
        file_path = self._resolve_audio_path(filename)
        return file_path.open("rb")
