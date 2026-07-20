from flask import Blueprint
import app.Controllers as Controllers
import app.Middlewares as Middlewares

diarization_transcription_bp = Blueprint("diarizationTranscription", __name__)

@diarization_transcription_bp.post("/createJob")
@Middlewares.check_audio
def uploadAudio():
    return Controllers.createDiarizationJob()

@diarization_transcription_bp.get("/result")
def getTranscription():
    return Controllers.getDiarizationByUuid()