from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager, suppress
from datetime import UTC, datetime
import errno
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import re
from shutil import copyfileobj
import stat
from typing import BinaryIO, Generator
from uuid import UUID, uuid4

from transcribe_ai_shared.storage.exceptions import (
    AudioAlreadyExistsError,
    AudioDirectoryChangedError,
    AudioNotFoundError,
    InvalidAudioLocationError,
)
from transcribe_ai_shared.storage.models import (
    AudioLocation,
    StorageScanResult,
    TranscriptionDirectory,
)


_TEMPORARY_AUDIO_FILENAME_PATTERN = re.compile(r"\.upload-[0-9a-f]{32}\.tmp")


def _is_audio_filename(job_uuid: UUID, filename: str) -> bool:
    """Réutilise l'invariant AudioLocation pour reconnaître un fichier final."""
    try:
        AudioLocation(uri=f"{job_uuid}/{filename}")
    except InvalidAudioLocationError:
        return False
    return True


class FileSystemAudioStorage:
    """Stockage audio confiné dans un dossier local ou un volume partagé."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        job_uuid: UUID,
        source: BinaryIO,
        *,
        extension: str,
    ) -> AudioLocation:
        """Publie atomiquement un flux complet sans exposer de fichier partiel."""
        normalized_extension = self._normalize_extension(extension)
        location = AudioLocation(
            uri=f"{job_uuid}/input.{normalized_extension}",
        )
        job_directory_name = str(job_uuid)
        final_filename = PurePosixPath(location.uri).name
        temporary_filename = f".upload-{uuid4().hex}.tmp"

        with self._open_root_directory() as root_fd:
            try:
                os.mkdir(job_directory_name, dir_fd=root_fd)
            except FileExistsError as error:
                raise AudioAlreadyExistsError(
                    f"Un audio existe déjà pour le job {job_uuid}."
                ) from error

            try:
                with self._open_child_directory(
                    root_fd,
                    job_directory_name,
                ) as job_directory_fd:
                    temporary_exists = False
                    try:
                        temporary_fd = os.open(
                            temporary_filename,
                            os.O_WRONLY
                            | os.O_CREAT
                            | os.O_EXCL
                            | os.O_CLOEXEC
                            | os.O_NOFOLLOW,
                            0o666,
                            dir_fd=job_directory_fd,
                        )
                        temporary_exists = True
                        with os.fdopen(temporary_fd, "wb") as destination:
                            copyfileobj(source, destination, length=1024 * 1024)
                            destination.flush()
                            os.fsync(destination.fileno())

                        os.replace(
                            temporary_filename,
                            final_filename,
                            src_dir_fd=job_directory_fd,
                            dst_dir_fd=job_directory_fd,
                        )
                        temporary_exists = False
                    except BaseException:
                        if temporary_exists:
                            with suppress(OSError):
                                os.unlink(
                                    temporary_filename,
                                    dir_fd=job_directory_fd,
                                )
                        raise
            except BaseException:
                with suppress(OSError):
                    os.rmdir(job_directory_name, dir_fd=root_fd)
                raise

        return location

    def open(
        self,
        location: AudioLocation,
    ) -> AbstractContextManager[BinaryIO]:
        """Ouvre un audio en lecture tout en garantissant la fermeture du flux."""
        return self._open_audio(location)

    def delete(self, location: AudioLocation) -> None:
        """Supprime l'audio de manière idempotente puis son dossier s'il est vide."""
        directory_name, filename = self._location_parts(location)

        with self._open_root_directory() as root_fd:
            try:
                job_directory_fd = self._open_child_directory_fd(
                    root_fd,
                    directory_name,
                )
            except FileNotFoundError:
                return
            except OSError as error:
                if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise InvalidAudioLocationError(
                        "La localisation audio traverse une entrée non sûre."
                    ) from error
                raise

            with self._close_file_descriptor(job_directory_fd):
                try:
                    file_stat = os.stat(
                        filename,
                        dir_fd=job_directory_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    self._remove_opened_empty_directory(
                        root_fd,
                        directory_name,
                        job_directory_fd,
                    )
                    return

                if not stat.S_ISREG(file_stat.st_mode):
                    raise InvalidAudioLocationError(
                        "La localisation audio ne cible pas un fichier régulier sûr."
                    )
                if not self._directory_is_attached(
                    root_fd,
                    directory_name,
                    job_directory_fd,
                ):
                    return

                try:
                    os.unlink(filename, dir_fd=job_directory_fd)
                except FileNotFoundError:
                    pass
                self._remove_opened_empty_directory(
                    root_fd,
                    directory_name,
                    job_directory_fd,
                )

    def scan_transcription_directories(self) -> StorageScanResult:
        """Inventorie les dossiers canoniques sans suivre de lien symbolique."""
        directories: list[TranscriptionDirectory] = []
        invalid_entry_count = 0
        unsafe_entry_count = 0
        error_count = 0

        with self._open_root_directory() as root_fd, os.scandir(root_fd) as entries:
            for entry in entries:
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(entry_stat.st_mode):
                        unsafe_entry_count += 1
                        continue
                    if not stat.S_ISDIR(entry_stat.st_mode):
                        continue
                except OSError:
                    error_count += 1
                    continue

                try:
                    job_uuid = UUID(entry.name)
                except ValueError:
                    invalid_entry_count += 1
                    continue
                if str(job_uuid) != entry.name:
                    invalid_entry_count += 1
                    continue

                try:
                    directories.append(
                        self._snapshot_transcription_directory(root_fd, job_uuid)
                    )
                except InvalidAudioLocationError:
                    unsafe_entry_count += 1
                except (OSError, OverflowError, ValueError):
                    error_count += 1

        return StorageScanResult(
            directories=tuple(directories),
            invalid_entry_count=invalid_entry_count,
            unsafe_entry_count=unsafe_entry_count,
            error_count=error_count,
        )

    def delete_transcription_directory(
        self,
        directory: TranscriptionDirectory,
    ) -> bool:
        """Supprime uniquement un dossier sûr resté identique depuis l'inventaire."""
        if not isinstance(directory, TranscriptionDirectory):
            raise InvalidAudioLocationError(
                "Un descripteur TranscriptionDirectory est requis."
            )

        directory_name = str(directory.job_uuid)
        with self._open_root_directory() as root_fd:
            try:
                job_directory_fd = self._open_child_directory_fd(
                    root_fd,
                    directory_name,
                )
            except FileNotFoundError:
                return False
            except OSError as error:
                raise InvalidAudioLocationError(
                    "Le dossier de transcription ne peut pas être ouvert sûrement."
                ) from error

            with self._close_file_descriptor(job_directory_fd):
                current, filenames = self._snapshot_open_directory(
                    job_directory_fd,
                    directory.job_uuid,
                )
                if current.revision != directory.revision:
                    raise AudioDirectoryChangedError(
                        f"Le dossier du job {directory.job_uuid} a changé depuis son inventaire."
                    )
                attachment_matches = self._directory_attachment_matches(
                    root_fd,
                    directory_name,
                    job_directory_fd,
                )
                if attachment_matches is None:
                    return False
                if not attachment_matches:
                    raise AudioDirectoryChangedError(
                        f"Le dossier du job {directory.job_uuid} a été remplacé."
                    )

                for filename in filenames:
                    try:
                        os.unlink(filename, dir_fd=job_directory_fd)
                    except FileNotFoundError:
                        continue

                attachment_matches = self._directory_attachment_matches(
                    root_fd,
                    directory_name,
                    job_directory_fd,
                )
                if attachment_matches is None:
                    return False
                if not attachment_matches:
                    raise AudioDirectoryChangedError(
                        f"Le dossier du job {directory.job_uuid} a été remplacé."
                    )

                try:
                    os.rmdir(directory_name, dir_fd=root_fd)
                except FileNotFoundError:
                    return False
                return True

    def _snapshot_transcription_directory(
        self,
        root_fd: int,
        job_uuid: UUID,
    ) -> TranscriptionDirectory:
        """Capture un descripteur immuable depuis un dossier ouvert sans symlink."""
        with self._open_child_directory(root_fd, str(job_uuid)) as directory_fd:
            snapshot, _ = self._snapshot_open_directory(directory_fd, job_uuid)
            return snapshot

    @staticmethod
    def _snapshot_open_directory(
        directory_fd: int,
        job_uuid: UUID,
    ) -> tuple[TranscriptionDirectory, tuple[str, ...]]:
        """Valide le contenu attendu et calcule un jeton de révision stable."""
        directory_stat = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise InvalidAudioLocationError(
                "L'entrée de transcription n'est pas un dossier sûr."
            )

        child_snapshots: list[tuple[str, int, int, int, int, int]] = []
        filenames: list[str] = []
        latest_mtime_ns = directory_stat.st_mtime_ns
        with os.scandir(directory_fd) as children:
            for child in children:
                child_stat = child.stat(follow_symlinks=False)
                if stat.S_ISLNK(child_stat.st_mode) or not stat.S_ISREG(
                    child_stat.st_mode
                ):
                    raise InvalidAudioLocationError(
                        "Un dossier de transcription contient une entrée non sûre."
                    )
                if not (
                    _is_audio_filename(job_uuid, child.name)
                    or _TEMPORARY_AUDIO_FILENAME_PATTERN.fullmatch(child.name)
                ):
                    raise InvalidAudioLocationError(
                        "Un dossier de transcription contient un fichier inattendu."
                    )
                filenames.append(child.name)
                child_snapshots.append(
                    (
                        child.name,
                        child_stat.st_mode,
                        child_stat.st_dev,
                        child_stat.st_ino,
                        child_stat.st_size,
                        child_stat.st_mtime_ns,
                    )
                )
                latest_mtime_ns = max(latest_mtime_ns, child_stat.st_mtime_ns)

        if len(filenames) > 1:
            raise InvalidAudioLocationError(
                "Un dossier de transcription ne peut contenir qu'un audio."
            )

        revision_source = (
            directory_stat.st_mode,
            directory_stat.st_dev,
            directory_stat.st_ino,
            directory_stat.st_mtime_ns,
            tuple(sorted(child_snapshots)),
        )
        revision = sha256(repr(revision_source).encode()).hexdigest()
        modified_at = datetime.fromtimestamp(
            latest_mtime_ns / 1_000_000_000,
            tz=UTC,
        )
        return (
            TranscriptionDirectory(
                job_uuid=job_uuid,
                modified_at=modified_at,
                revision=revision,
            ),
            tuple(filenames),
        )

    @contextmanager
    def _open_root_directory(self) -> Generator[int, None, None]:
        """Ouvre la racine résolue avec les protections Linux anti-symlink."""
        directory_fd = os.open(
            self.root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        with self._close_file_descriptor(directory_fd):
            yield directory_fd

    @classmethod
    @contextmanager
    def _open_child_directory(
        cls,
        root_fd: int,
        directory_name: str,
    ) -> Generator[int, None, None]:
        directory_fd = cls._open_child_directory_fd(root_fd, directory_name)
        with cls._close_file_descriptor(directory_fd):
            yield directory_fd

    @staticmethod
    def _open_child_directory_fd(root_fd: int, directory_name: str) -> int:
        return os.open(
            directory_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=root_fd,
        )

    @staticmethod
    @contextmanager
    def _close_file_descriptor(
        file_descriptor: int,
    ) -> Generator[int, None, None]:
        try:
            yield file_descriptor
        finally:
            os.close(file_descriptor)

    @staticmethod
    def _normalize_extension(extension: str) -> str:
        """Normalise une extension simple sans accepter de composant de chemin."""
        if not isinstance(extension, str):
            raise InvalidAudioLocationError("L'extension audio doit être une chaîne.")

        normalized_extension = extension.lower()
        if normalized_extension.startswith("."):
            normalized_extension = normalized_extension[1:]

        return normalized_extension

    @staticmethod
    def _location_parts(location: AudioLocation) -> tuple[str, str]:
        """Retourne les deux composants déjà validés d'une clé de stockage."""
        if not isinstance(location, AudioLocation):
            raise InvalidAudioLocationError(
                "Une instance AudioLocation est requise pour accéder au stockage."
            )
        relative_path = PurePosixPath(location.uri)
        return relative_path.parts[0], relative_path.parts[1]

    @contextmanager
    def _open_audio(
        self,
        location: AudioLocation,
    ) -> Generator[BinaryIO, None, None]:
        """Maintient ouverts les descripteurs confinés pendant toute la lecture."""
        directory_name, filename = self._location_parts(location)

        with self._open_root_directory() as root_fd:
            try:
                job_directory_fd = self._open_child_directory_fd(
                    root_fd,
                    directory_name,
                )
            except FileNotFoundError as error:
                raise AudioNotFoundError(
                    f"L'audio {location.uri} n'existe pas."
                ) from error
            except OSError as error:
                if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise InvalidAudioLocationError(
                        "La localisation audio traverse une entrée non sûre."
                    ) from error
                raise

            with self._close_file_descriptor(job_directory_fd):
                try:
                    audio_file_fd = os.open(
                        filename,
                        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                        dir_fd=job_directory_fd,
                    )
                except FileNotFoundError as error:
                    raise AudioNotFoundError(
                        f"L'audio {location.uri} n'existe pas."
                    ) from error
                except OSError as error:
                    if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                        raise InvalidAudioLocationError(
                            "La localisation audio ne cible pas un fichier sûr."
                        ) from error
                    raise

                try:
                    if not stat.S_ISREG(os.fstat(audio_file_fd).st_mode):
                        raise InvalidAudioLocationError(
                            "La localisation audio ne cible pas un fichier régulier sûr."
                        )
                except BaseException:
                    os.close(audio_file_fd)
                    raise

                with os.fdopen(audio_file_fd, "rb") as audio_file:
                    yield audio_file

    @staticmethod
    def _directory_is_attached(
        root_fd: int,
        directory_name: str,
        directory_fd: int,
    ) -> bool:
        """Vérifie que le descripteur désigne toujours l'entrée sous la racine."""
        return (
            FileSystemAudioStorage._directory_attachment_matches(
                root_fd,
                directory_name,
                directory_fd,
            )
            is True
        )

    @staticmethod
    def _directory_attachment_matches(
        root_fd: int,
        directory_name: str,
        directory_fd: int,
    ) -> bool | None:
        """Distingue une entrée absente d'un dossier remplacé concurremment."""
        try:
            root_entry_stat = os.stat(
                directory_name,
                dir_fd=root_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None

        opened_directory_stat = os.fstat(directory_fd)
        return (
            root_entry_stat.st_dev,
            root_entry_stat.st_ino,
        ) == (
            opened_directory_stat.st_dev,
            opened_directory_stat.st_ino,
        )

    @classmethod
    def _remove_opened_empty_directory(
        cls,
        root_fd: int,
        directory_name: str,
        directory_fd: int,
    ) -> None:
        """Retire seulement le dossier vide encore rattaché à la racine."""
        if not cls._directory_is_attached(
            root_fd,
            directory_name,
            directory_fd,
        ):
            return
        try:
            os.rmdir(directory_name, dir_fd=root_fd)
        except FileNotFoundError:
            return
        except OSError as error:
            if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                raise
