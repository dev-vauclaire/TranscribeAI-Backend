class AudioAlreadyExistsError(FileExistsError):
    """Erreur levée lorsqu'un audio existe déjà pour le job."""


class AudioNotFoundError(FileNotFoundError):
    """Erreur levée lorsque l'audio demandé n'existe pas."""


class AudioDirectoryChangedError(RuntimeError):
    """Erreur levée lorsqu'un dossier change entre son scan et sa suppression."""


class InvalidAudioLocationError(ValueError):
    """Erreur levée lorsqu'une localisation audio n'est pas sûre ou valide."""
