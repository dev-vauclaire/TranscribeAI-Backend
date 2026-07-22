import pytest

from transcribe_ai_shared.database.models.job_model import Job, JobType
from transcribe_ai_shared.database.repositories.job_repository import JobRepository
from transcribe_ai_shared.services import AudioManager, UploadedAudio


pytestmark = pytest.mark.integration


def test_job_filename_round_trip_between_database_and_audio_storage(
    db_session,
    tmp_path,
):
    audio_folder = tmp_path / "audio"
    first_manager = AudioManager(str(audio_folder))
    filename = first_manager.save_audio(
        UploadedAudio(filename="job.wav", content=b"audio content")
    )
    job = JobRepository(db_session).add(
        Job(type=JobType.MONO_VOICE, filename=filename)
    )
    db_session.commit()
    job_id = job.id
    db_session.expire_all()

    saved_job = JobRepository(db_session).get_by_id(job_id)
    second_manager = AudioManager(str(audio_folder))

    with second_manager.open_audio(saved_job.filename) as audio_stream:
        assert audio_stream.read() == b"audio content"

    assert saved_job.filename == "job.wav"
