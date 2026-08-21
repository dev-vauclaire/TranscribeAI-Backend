from transcribe_ai_shared.storage.models import AudioLocation
from transcribe_ai_shared.worker.models import TranscriptionOutput


class FakeTranscriber:
    """Double déterministe réservé aux tests et aux compositions de développement."""

    def __init__(
        self,
        output: TranscriptionOutput | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._output = output or TranscriptionOutput(
            result={"text": "fake transcription"},
        )
        self._error = error
        self._calls: list[AudioLocation] = []

    @property
    def calls(self) -> tuple[AudioLocation, ...]:
        return tuple(self._calls)

    async def transcribe(
        self,
        audio_location: AudioLocation,
    ) -> TranscriptionOutput:
        self._calls.append(audio_location)
        if self._error is not None:
            raise self._error
        return self._output
