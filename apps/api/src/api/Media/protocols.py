from typing import BinaryIO, Protocol

from api.Media.models import AudioMetadata


class MediaProbe(Protocol):
    """Contrat d'inspection du contenu réel d'un média stocké."""

    def probe(self, source: BinaryIO) -> AudioMetadata:
        """Inspecte le flux depuis sa position courante sans le fermer."""
        ...
