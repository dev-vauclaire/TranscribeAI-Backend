from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, NoReturn, TypeVar

import redis.asyncio as redis_asyncio
from redis import exceptions as redis_exceptions

from transcribe_ai_shared.database.models import JobType
from transcribe_ai_shared.queue.exceptions import (
    RedisConnectionError,
    RedisOperationError,
)
from transcribe_ai_shared.queue.models import (
    AutoClaimResult,
    JobStreamMessage,
    ReceivedJobStreamMessage,
    stream_name_for_job_type,
)
from transcribe_ai_shared.queue.serialization import JobStreamMessageCodec


_T = TypeVar("_T")
_RedisEntry = Sequence[Any]


class RedisTranscriptionStreams:
    """Implémentation Redis Streams des primitives de distribution des jobs."""

    def __init__(
        self,
        redis_url: str,
        *,
        socket_timeout_seconds: float = 10.0,
        socket_connect_timeout_seconds: float = 5.0,
    ) -> None:
        if socket_timeout_seconds <= 0:
            raise ValueError("socket_timeout_seconds doit être strictement positif")
        if socket_connect_timeout_seconds <= 0:
            raise ValueError(
                "socket_connect_timeout_seconds doit être strictement positif"
            )

        self._redis = redis_asyncio.Redis.from_url(
            redis_url,
            decode_responses=False,
            socket_timeout=socket_timeout_seconds,
            socket_connect_timeout=socket_connect_timeout_seconds,
        )
        self._codec = JobStreamMessageCodec()
        self._socket_timeout_milliseconds = socket_timeout_seconds * 1_000

    async def aclose(self) -> None:
        """Ferme les connexions détenues par le client Redis."""
        await self._execute_redis("fermer la connexion Redis", self._redis.aclose)

    async def publish(self, message: JobStreamMessage) -> str:
        stream_name = stream_name_for_job_type(message.job_type)
        payload = self._codec.serialize(message)
        redis_message_id = await self._execute_redis(
            "publier un job",
            lambda: self._redis.xadd(stream_name, payload),
        )
        return self._decode_text(redis_message_id, "identifiant retourné par XADD")

    async def ensure_consumer_group(
        self,
        job_type: JobType,
        group_name: str,
    ) -> None:
        """Crée un groupe depuis l'origine sans ignorer les messages déjà publiés."""
        self._validate_name(group_name, "group_name")
        stream_name = stream_name_for_job_type(job_type)

        try:
            await self._redis.xgroup_create(
                stream_name,
                group_name,
                id="0-0",
                mkstream=True,
            )
        except redis_exceptions.ResponseError as error:
            if self._is_busy_group_error(error):
                return
            self._raise_translated_error("créer le consumer group", error)
        except redis_exceptions.RedisError as error:
            self._raise_translated_error("créer le consumer group", error)

    async def consume(
        self,
        job_type: JobType,
        group_name: str,
        consumer_name: str,
        *,
        block_milliseconds: int | None = 5_000,
    ) -> ReceivedJobStreamMessage | None:
        self._validate_name(group_name, "group_name")
        self._validate_name(consumer_name, "consumer_name")
        if block_milliseconds is not None:
            self._validate_positive_integer(
                block_milliseconds,
                "block_milliseconds",
            )
            if block_milliseconds >= self._socket_timeout_milliseconds:
                raise ValueError(
                    "block_milliseconds doit être inférieur au timeout socket"
                )

        stream_name = stream_name_for_job_type(job_type)
        response = await self._execute_redis(
            "consommer un job",
            lambda: self._redis.xreadgroup(
                groupname=group_name,
                consumername=consumer_name,
                streams={stream_name: ">"},
                count=1,
                block=block_milliseconds,
                noack=False,
            ),
        )
        if not response:
            return None

        entries = self._extract_read_group_entries(response)
        if not entries:
            return None
        return self._deserialize_entry(entries[0], job_type)

    async def ack_and_delete(
        self,
        group_name: str,
        message: ReceivedJobStreamMessage,
    ) -> bool:
        """Acquitte puis supprime atomiquement une entrée avec ``XACKDEL``."""
        self._validate_name(group_name, "group_name")
        self._validate_name(message.redis_message_id, "redis_message_id")
        stream_name = stream_name_for_job_type(message.job_type)

        result = await self._execute_redis(
            "acquitter et supprimer un job",
            lambda: self._redis.xackdel(
                stream_name,
                group_name,
                message.redis_message_id,
                ref_policy="KEEPREF",
            ),
        )
        return self._normalize_ack_result(result)

    async def autoclaim(
        self,
        job_type: JobType,
        group_name: str,
        consumer_name: str,
        *,
        min_idle_milliseconds: int,
        start_id: str = "0-0",
        count: int = 1,
    ) -> AutoClaimResult:
        """Transfère des pending expirés sans appliquer de politique métier."""
        self._validate_name(group_name, "group_name")
        self._validate_name(consumer_name, "consumer_name")
        self._validate_name(start_id, "start_id")
        self._validate_non_negative_integer(
            min_idle_milliseconds,
            "min_idle_milliseconds",
        )
        self._validate_positive_integer(count, "count")

        stream_name = stream_name_for_job_type(job_type)
        response = await self._execute_redis(
            "réattribuer les jobs pending",
            lambda: self._redis.xautoclaim(
                name=stream_name,
                groupname=group_name,
                consumername=consumer_name,
                min_idle_time=min_idle_milliseconds,
                start_id=start_id,
                count=count,
                justid=False,
            ),
        )
        return self._deserialize_autoclaim_response(response, job_type)

    async def _execute_redis(
        self,
        operation: str,
        command: Callable[[], Awaitable[_T]],
    ) -> _T:
        try:
            return await command()
        except redis_exceptions.RedisError as error:
            self._raise_translated_error(operation, error)

    def _deserialize_autoclaim_response(
        self,
        response: Any,
        job_type: JobType,
    ) -> AutoClaimResult:
        if not isinstance(response, Sequence) or isinstance(response, (str, bytes)):
            self._raise_unexpected_response("XAUTOCLAIM")
        if len(response) not in {2, 3}:
            self._raise_unexpected_response("XAUTOCLAIM")

        next_start_id = self._decode_text(response[0], "curseur XAUTOCLAIM")
        raw_entries = response[1]
        if not isinstance(raw_entries, Sequence) or isinstance(
            raw_entries,
            (str, bytes),
        ):
            self._raise_unexpected_response("XAUTOCLAIM")

        messages = tuple(
            self._deserialize_entry(raw_entry, job_type) for raw_entry in raw_entries
        )
        raw_deleted_ids = response[2] if len(response) == 3 else ()
        if not isinstance(raw_deleted_ids, Sequence) or isinstance(
            raw_deleted_ids,
            (str, bytes),
        ):
            self._raise_unexpected_response("XAUTOCLAIM")
        deleted_message_ids = tuple(
            self._decode_text(raw_id, "identifiant supprimé par XAUTOCLAIM")
            for raw_id in raw_deleted_ids
        )

        return AutoClaimResult(
            next_start_id=next_start_id,
            messages=messages,
            deleted_message_ids=deleted_message_ids,
        )

    def _deserialize_entry(
        self,
        raw_entry: Any,
        job_type: JobType,
    ) -> ReceivedJobStreamMessage:
        if not isinstance(raw_entry, Sequence) or isinstance(raw_entry, (str, bytes)):
            self._raise_unexpected_response("Redis Streams")
        if len(raw_entry) != 2:
            self._raise_unexpected_response("Redis Streams")

        redis_message_id = self._decode_text(
            raw_entry[0],
            "identifiant du message Redis",
        )
        payload = raw_entry[1]
        if not isinstance(payload, Mapping):
            self._raise_unexpected_response("Redis Streams")
        return self._codec.deserialize(redis_message_id, job_type, payload)

    @staticmethod
    def _extract_read_group_entries(response: Any) -> Sequence[_RedisEntry]:
        if not isinstance(response, Sequence) or isinstance(response, (str, bytes)):
            RedisTranscriptionStreams._raise_unexpected_response("XREADGROUP")
        if len(response) != 1:
            RedisTranscriptionStreams._raise_unexpected_response("XREADGROUP")

        stream_response = response[0]
        if not isinstance(stream_response, Sequence) or isinstance(
            stream_response,
            (str, bytes),
        ):
            RedisTranscriptionStreams._raise_unexpected_response("XREADGROUP")
        if len(stream_response) != 2:
            RedisTranscriptionStreams._raise_unexpected_response("XREADGROUP")

        entries = stream_response[1]
        if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
            RedisTranscriptionStreams._raise_unexpected_response("XREADGROUP")
        return entries

    @staticmethod
    def _normalize_ack_result(result: Any) -> bool:
        # XACKDEL renvoie un statut par ID, malgré l'annotation scalaire de redis-py.
        if isinstance(result, Sequence) and not isinstance(result, (str, bytes)):
            if len(result) == 1:
                status = result[0]
                if type(status) is int and status in {-1, 1}:
                    return status == 1
        RedisTranscriptionStreams._raise_unexpected_response("XACKDEL")

    @staticmethod
    def _decode_text(value: Any, description: str) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError as error:
                raise RedisOperationError(
                    f"Réponse Redis invalide pour {description}"
                ) from error
        raise RedisOperationError(f"Réponse Redis invalide pour {description}")

    @staticmethod
    def _is_busy_group_error(error: redis_exceptions.ResponseError) -> bool:
        return str(error).lstrip().upper().startswith("BUSYGROUP")

    @staticmethod
    def _validate_name(value: str, parameter_name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{parameter_name} ne peut pas être vide")

    @staticmethod
    def _validate_non_negative_integer(value: int, parameter_name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{parameter_name} doit être un entier positif ou nul")

    @classmethod
    def _validate_positive_integer(cls, value: int, parameter_name: str) -> None:
        cls._validate_non_negative_integer(value, parameter_name)
        if value == 0:
            raise ValueError(f"{parameter_name} doit être strictement positif")

    @staticmethod
    def _raise_translated_error(
        operation: str,
        error: redis_exceptions.RedisError,
    ) -> NoReturn:
        if isinstance(
            error,
            (redis_exceptions.ConnectionError, redis_exceptions.TimeoutError),
        ):
            raise RedisConnectionError(
                f"Impossible de {operation} : Redis est indisponible"
            ) from error
        raise RedisOperationError(f"Impossible de {operation}") from error

    @staticmethod
    def _raise_unexpected_response(command: str) -> NoReturn:
        raise RedisOperationError(f"Réponse {command} inattendue")
