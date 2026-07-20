from app.Routes.batchTranscriptionRoutes import batch_transcription_bp
from app.Routes.diarizationTranscriptionRoutes import diarization_transcription_bp
import app.Middlewares.check_key_middleware as check_key_middleware

# Register all blueprints with the Flask app
def register_routes(app):
    app.before_request(check_key_middleware)
    url_prefix = "/api"
    app.register_blueprint(batch_transcription_bp, url_prefix=url_prefix + "/batchTranscription")
    app.register_blueprint(diarization_transcription_bp, url_prefix=url_prefix + "/diarizationTranscription")
