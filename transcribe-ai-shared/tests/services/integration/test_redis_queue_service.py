import pytest


pytestmark = pytest.mark.integration


def test_push_job_stores_and_returns_job_uuid(redis_queue_service):
    job_uuid = "2c0672d8-6f95-4bdf-848b-060ec2f264d2"

    result = redis_queue_service.push_job(job_uuid)

    assert result == job_uuid
    assert redis_queue_service.redis.lrange(redis_queue_service.queue_name, 0, -1) == [
        job_uuid
    ]


def test_queue_is_fifo(redis_queue_service):
    first_job = "first-job"
    second_job = "second-job"
    redis_queue_service.push_job(first_job)
    redis_queue_service.push_job(second_job)

    assert redis_queue_service.pop_job() == first_job
    assert redis_queue_service.pop_job() == second_job


def test_get_queue_position_is_one_based(redis_queue_service):
    redis_queue_service.push_job("first-job")
    redis_queue_service.push_job("second-job")
    redis_queue_service.push_job("third-job")

    assert redis_queue_service.get_queue_position("first-job") == 1
    assert redis_queue_service.get_queue_position("second-job") == 2
    assert redis_queue_service.get_queue_position("third-job") == 3


def test_get_queue_position_returns_none_for_unknown_job(redis_queue_service):
    redis_queue_service.push_job("known-job")

    assert redis_queue_service.get_queue_position("unknown-job") is None


def test_positions_are_updated_after_pop(redis_queue_service):
    redis_queue_service.push_job("first-job")
    redis_queue_service.push_job("second-job")

    assert redis_queue_service.pop_job() == "first-job"
    assert redis_queue_service.get_queue_position("first-job") is None
    assert redis_queue_service.get_queue_position("second-job") == 1
