import redis

# Service pour gérer une file Redis simple (FIFO)
class RedisQueueService:
    def __init__(self, redis_url: str, queue_name: str):
        self.redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self.queue_name = queue_name

    # Enfile un job_id
    def push_job(self, job_uuid: str):
        self.redis.rpush(self.queue_name, job_uuid)
        return job_uuid

    # Défile un job (bloquant)
    def pop_job(self) -> str:
        _, job_uuid = self.redis.blpop(self.queue_name)
        return job_uuid
    
    def get_queue_position(self, job_uuid: str) -> int | None:
        """
        Retourne la position (1-based) de l'élément dans la liste Redis.
        Retourne None si l'élément n'est pas dans la liste.
        """
        # lpos retourne l'index (0-based) de l'élément
        index = self.redis.execute_command('LPOS', self.queue_name, job_uuid)
        if index is not None:
            return index + 1
        return None