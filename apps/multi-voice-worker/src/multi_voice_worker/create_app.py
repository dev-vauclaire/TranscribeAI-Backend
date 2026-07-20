from common_packages import create_app

# Configuration spécifique au worker de diarization
def create_app_worker_multi_voice(config_class):

    app = create_app(config_class)

    from common_packages.Services.DiarizationService import DiarizationService
    app.extensions['diarization_service'] = DiarizationService(app.config['WHISPERX_SERVICE_URL'])
    from common_packages.Services.RedisQueueService import RedisQueueService
    app.extensions['redis_diarization_queue_service'] = RedisQueueService(app.config['REDIS_URL'], "diarization_job_queue")

    return app