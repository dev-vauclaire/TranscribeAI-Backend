import pytest

from transcribe_ai_shared.database.base import NAMING_CONVENTION
from transcribe_ai_shared.database.models.job_model import Job, JobStatus, JobType


pytestmark = pytest.mark.unit


def test_job_enums_match_shared_contract():
    assert [status.value for status in JobStatus] == [
        "PENDING",
        "PROCESSING",
        "COMPLETED",
        "FAILED",
    ]
    assert [job_type.value for job_type in JobType] == [
        "MONO_VOICE",
        "MULTI_VOICE",
    ]


def test_job_uses_shared_metadata_naming_convention():
    assert Job.metadata.naming_convention == NAMING_CONVENTION
