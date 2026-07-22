import redis


# Service pour gérer une file Redis simple (FIFO)
class RedisQueueService:
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
            raise ConnectionError("Impossible de se connecter à Redis") from error

    # Enfile un job_id
    def push_job(self, job_uuid: str) -> str:
        self.redis.rpush(self.queue_name, job_uuid)
        return job_uuid

    # Attend un job pendant une durée bornée
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
        """
        Retourne la position (1-based) de l'élément dans la liste Redis.
        Retourne None si l'élément n'est pas dans la liste.
        """
        # lpos retourne l'index (0-based) de l'élément
        index = self.redis.execute_command("LPOS", self.queue_name, job_uuid)
        if index is not None:
            return index + 1
        return None
