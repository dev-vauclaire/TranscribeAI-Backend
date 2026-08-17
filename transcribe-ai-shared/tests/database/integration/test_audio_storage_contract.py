import pytest

from transcribe_ai_shared.database import (
    JobType,
    TranscriptionJob,
    TranscriptionJobRepository,
)
from transcribe_ai_shared.storage import AudioStorageService, UploadedAudio


pytestmark = pytest.mark.integration


def test_job_filename_round_trip_between_database_and_audio_storage(
    db_session,
    tmp_path,
):
    audio_folder = tmp_path / "audio"
    first_storage = AudioStorageService(audio_folder)
    filename = first_storage.save_audio(
        UploadedAudio(filename="job.wav", content=b"audio content")
    )
    job = TranscriptionJobRepository(db_session).add(
        TranscriptionJob(job_type=JobType.FAST, audio_uri=filename)
    )
    db_session.commit()
    job_uuid = job.job_uuid
    db_session.expire_all()

    saved_job = TranscriptionJobRepository(db_session).get_by_uuid(job_uuid)
    assert saved_job is not None
    second_storage = AudioStorageService(audio_folder)

    with second_storage.open_audio(saved_job.audio_uri) as audio_stream:
        assert audio_stream.read() == b"audio content"

    assert saved_job.audio_uri == "job.wav"
