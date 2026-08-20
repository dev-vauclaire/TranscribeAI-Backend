from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias
from uuid import UUID

from transcribe_ai_shared.database.models.enums import JobType
from transcribe_ai_shared.queue.exceptions import InvalidJobStreamMessageError
from transcribe_ai_shared.queue.models import (
    JobStreamMessage,
    MAX_ATTEMPT_COUNT,
    ReceivedJobStreamMessage,
)


RedisPayloadValue: TypeAlias = str | bytes
RedisPayload: TypeAlias = Mapping[RedisPayloadValue, RedisPayloadValue]


class JobStreamMessageCodec:
    """Traduit les messages typés vers le format plat de Redis Streams."""

    @staticmethod
    def serialize(message: JobStreamMessage) -> dict[str, str]:
        """Sérialise seulement les données qui ne sont pas portées par le stream."""
        if not isinstance(message, JobStreamMessage):
            raise InvalidJobStreamMessageError(
                "message doit être une instance de JobStreamMessage"
            )

        return {
            "job_uuid": str(message.job_uuid),
            "attempt_count": str(message.attempt_count),
        }

    @classmethod
    def deserialize(
        cls,
        redis_message_id: RedisPayloadValue,
        job_type: JobType,
        payload: RedisPayload,
    ) -> ReceivedJobStreamMessage:
        """Valide un payload externe sans inclure son contenu dans les erreurs."""
        normalized_message_id = cls._decode_text(
            redis_message_id,
            field_name="redis_message_id",
        )

        try:
            raw_job_uuid = cls._required_value(payload, "job_uuid")
            raw_attempt_count = cls._required_value(payload, "attempt_count")
            job_uuid_text = cls._decode_text(raw_job_uuid, field_name="job_uuid")
            job_uuid = UUID(job_uuid_text)
            if str(job_uuid) != job_uuid_text:
                raise InvalidJobStreamMessageError(
                    "job_uuid doit être un UUID canonique"
                )
            attempt_count_text = cls._decode_text(
                raw_attempt_count,
                field_name="attempt_count",
            )
            attempt_count = cls._parse_attempt_count(attempt_count_text)
            return ReceivedJobStreamMessage(
                redis_message_id=normalized_message_id,
                job_uuid=job_uuid,
                job_type=job_type,
                attempt_count=attempt_count,
            )
        except InvalidJobStreamMessageError as error:
            if error.redis_message_id is not None:
                raise
            raise InvalidJobStreamMessageError(
                error.reason,
                redis_message_id=normalized_message_id,
            ) from error
        except (TypeError, ValueError) as error:
            raise InvalidJobStreamMessageError(
                "job_uuid doit être un UUID canonique",
                redis_message_id=normalized_message_id,
            ) from error

    @staticmethod
    def _required_value(
        payload: RedisPayload,
        field_name: str,
    ) -> RedisPayloadValue:
        if not isinstance(payload, Mapping):
            raise InvalidJobStreamMessageError("payload doit être un mapping")

        string_present = field_name in payload
        byte_field_name = field_name.encode()
        bytes_present = byte_field_name in payload
        if string_present and bytes_present:
            raise InvalidJobStreamMessageError(
                f"le champ {field_name} est présent plusieurs fois"
            )
        if string_present:
            return payload[field_name]
        if bytes_present:
            return payload[byte_field_name]
        raise InvalidJobStreamMessageError(f"le champ {field_name} est obligatoire")

    @staticmethod
    def _decode_text(
        value: RedisPayloadValue,
        *,
        field_name: str,
    ) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError as error:
                raise InvalidJobStreamMessageError(
                    f"le champ {field_name} doit être encodé en UTF-8"
                ) from error
        raise InvalidJobStreamMessageError(
            f"le champ {field_name} doit être une chaîne"
        )

    @staticmethod
    def _parse_attempt_count(value: str) -> int:
        if not value.isascii() or not value.isdecimal():
            raise InvalidJobStreamMessageError(
                "attempt_count doit être un entier décimal canonique"
            )

        attempt_count = int(value)
        if str(attempt_count) != value or attempt_count > MAX_ATTEMPT_COUNT:
            raise InvalidJobStreamMessageError(
                "attempt_count doit être compris entre 0 et 2147483647"
            )
        return attempt_count
