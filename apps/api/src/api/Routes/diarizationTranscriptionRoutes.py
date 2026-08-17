from flask import Blueprint
import api.Controllers as Controllers
import api.Middlewares as Middlewares

diarization_transcription_bp = Blueprint("diarizationTranscription", __name__)


@diarization_transcription_bp.post("/createJob")
@Middlewares.check_audio
def uploadAudio():
    return Controllers.createDiarizationJob()


@diarization_transcription_bp.get("/result")
def getTranscription():
    return Controllers.getDiarizationByUuid()
