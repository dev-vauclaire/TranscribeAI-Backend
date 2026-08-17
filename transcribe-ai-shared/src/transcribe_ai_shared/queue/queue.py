import redis

from transcribe_ai_shared.queue.exceptions import RedisConnectionError


class RedisQueueService:
    """File FIFO Redis utilisée pour publier et consommer des jobs."""

    def __init__(
        self,
        redis_url: str,
        queue_name: str,
        *,
        pop_timeout_seconds: float = 5.0,
        socket_timeout_seconds: float = 10.0,
        socket_connect_timeout_seconds: float = 5.0,
    ) -> None:
        if pop_timeout_seconds <= 0:
            raise ValueError("pop_timeout_seconds doit être strictement positif")
        if socket_timeout_seconds <= pop_timeout_seconds:
            raise ValueError(
                "socket_timeout_seconds doit être supérieur à pop_timeout_seconds"
            )
        if socket_connect_timeout_seconds <= 0:
            raise ValueError(
                "socket_connect_timeout_seconds doit être strictement positif"
            )

        self.redis = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=socket_timeout_seconds,
            socket_connect_timeout=socket_connect_timeout_seconds,
        )
        self.queue_name = queue_name
        self.pop_timeout_seconds = pop_timeout_seconds

    def check_redis_connection(self) -> None:
        try:
            self.redis.ping()
        except redis.RedisError as error:
            raise RedisConnectionError("Impossible de se connecter à Redis") from error

    def push_job(self, job_uuid: str) -> str:
        self.redis.rpush(self.queue_name, job_uuid)
        return job_uuid

    def pop_job(self) -> str | None:
        result = self.redis.blpop(
            self.queue_name,
            timeout=self.pop_timeout_seconds,
        )
        if result is None:
            return None

        _, job_uuid = result
        return job_uuid

    def get_queue_position(self, job_uuid: str) -> int | None:
        """Return the one-based position of a job, or ``None`` when absent."""
        index = self.redis.execute_command("LPOS", self.queue_name, job_uuid)
        if index is None:
            return None
        return index + 1
