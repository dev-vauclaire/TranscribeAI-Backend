from flask import Blueprint
import app.Controllers as Controllers
import app.Middlewares as Middlewares

batch_transcription_bp = Blueprint("batchTranscription", __name__)

@batch_transcription_bp.post("/createJob")
@Middlewares.check_audio
def uploadAudio():
    return Controllers.createBatchJob()

@batch_transcription_bp.get("/result")
def getTranscription():
    return Controllers.getBatchTranscriptionByUuid()

@batch_transcription_bp.delete("/cancel")
def deleteTranscription():
    return Controllers.deleteTranscription()
