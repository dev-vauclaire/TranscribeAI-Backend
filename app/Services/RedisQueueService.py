import redis

# Service pour gérer une file Redis simple (FIFO)
class RedisQueueService:
    def __init__(self, redis_url: str, queue_name: str = "job_queue"):
        self.redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self.queue_name = queue_name

    # Enfile un job_id
    def enqueue_job(self, job_id: str):
        self.redis.rpush(self.queue_name, job_id)
        return job_id

    # Défile un job (bloquant)
    def pop_job_blocking(self) -> str:
        _, job_id = self.redis.blpop(self.queue_name)
        return job_id
    
    def get_queue_position(self, job_id):
        """
        Retourne la position (1-based) de l'élément dans la liste Redis.
        Retourne None si l'élément n'est pas dans la liste.
        """
        # lpos retourne l'index (0-based) de l'élément
        index = self.redis.execute_command('LPOS', self.queue_name, job_id)
        if index is not None:
            return index + 1
        return None