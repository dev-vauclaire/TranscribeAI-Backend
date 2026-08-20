class RedisConnectionError(ConnectionError):
    """Erreur levée lorsque Redis est indisponible."""


class RedisOperationError(RuntimeError):
    """Erreur levée lorsqu'une commande Redis échoue hors indisponibilité."""


class InvalidJobStreamMessageError(ValueError):
    """Erreur levée lorsqu'un message de job ne respecte pas le contrat Redis."""

    def __init__(
        self,
        reason: str,
        *,
        redis_message_id: str | None = None,
    ) -> None:
        self.reason = reason
        self.redis_message_id = redis_message_id

        if redis_message_id is None:
            message = f"Message de job Redis invalide : {reason}"
        else:
            message = f"Message de job Redis {redis_message_id!r} invalide : {reason}"

        super().__init__(message)
