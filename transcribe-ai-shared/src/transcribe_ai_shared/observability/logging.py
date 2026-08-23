"""Journalisation JSON structurée et restrictive pour tous les services."""

import json
import logging
from datetime import UTC, date, datetime
from enum import Enum
from typing import Final
from uuid import UUID


_CORRELATION_FIELDS: Final = (
    "job_uuid",
    "attempt_count",
    "worker_id",
    "redis_message_id",
)

# Ce contrat explicite empêche qu'un objet métier, un payload audio, un texte
# transcrit ou une exception complète soit sérialisé par inadvertance. Les
# valeurs de ces champs doivent rester des codes ou des compteurs contrôlés.
_SAFE_DETAIL_FIELDS: Final = frozenset(
    {
        "action",
        "confirmed_count",
        "deleted_count",
        "dependency",
        "error_count",
        "error_type",
        "failed_count",
        "failure_category",
        "failure_code",
        "inspected_count",
        "job_type",
        "kept_count",
        "next_attempt_count",
        "orphan_deleted_count",
        "published_count",
        "reason",
        "rearmed_count",
        "requeued_count",
        "result_type",
        "selected_count",
        "source",
        "stale_count",
        "status",
        "too_recent_count",
    }
)
_STRUCTURED_FIELDS: Final = frozenset(_CORRELATION_FIELDS) | _SAFE_DETAIL_FIELDS
_UNSTRUCTURED_EVENT: Final = "unstructured_log"
_UVICORN_ACCESS_EVENT: Final = "http_request_completed"
_HTTP_METHODS: Final = frozenset(
    {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}
)


def _validate_label(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} doit être une chaîne non vide")
    return value.strip()


def _normalize_value(value: object) -> str | int | float | bool | None:
    """Retourne uniquement des scalaires sûrs, sans appeler un ``repr`` arbitraire."""
    if isinstance(value, Enum):
        return _normalize_value(value.value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return type(value).__name__


def _normalize_job_uuid(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        try:
            return str(UUID(value))
        except ValueError:
            return None
    return None


def _safe_context(
    *,
    job_uuid: object = None,
    attempt_count: object = None,
    worker_id: object = None,
    redis_message_id: object = None,
    details: dict[str, object],
) -> dict[str, str | int | float | bool]:
    context: dict[str, str | int | float | bool] = {}
    normalized_uuid = _normalize_job_uuid(job_uuid)
    if normalized_uuid is not None:
        context["job_uuid"] = normalized_uuid

    correlations = {
        "attempt_count": attempt_count,
        "worker_id": worker_id,
        "redis_message_id": redis_message_id,
    }
    for field_name, value in correlations.items():
        normalized = _normalize_value(value)
        if normalized is not None:
            context[field_name] = normalized

    for field_name, value in details.items():
        if field_name not in _SAFE_DETAIL_FIELDS:
            continue
        normalized = _normalize_value(value)
        if normalized is not None:
            context[field_name] = normalized
    return context


def _uvicorn_access_context(
    record: logging.LogRecord,
) -> dict[str, str | int]:
    """Extrait méthode/statut sans journaliser client, chemin ou query string."""
    if record.name != "uvicorn.access" or not isinstance(record.args, tuple):
        return {}
    if len(record.args) != 5:
        return {}

    method = record.args[1]
    status_code = record.args[4]
    context: dict[str, str | int] = {}
    if isinstance(method, str) and method in _HTTP_METHODS:
        context["http_method"] = method
    if (
        isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and 100 <= status_code <= 599
    ):
        context["http_status_code"] = status_code
    return context


def log_event(
    logger: logging.Logger,
    level: int,
    *,
    service: str,
    event: str,
    job_uuid: object = None,
    attempt_count: object = None,
    worker_id: object = None,
    redis_message_id: object = None,
    **details: object,
) -> None:
    """Journalise un événement sans sérialiser de données hors contrat.

    Les champs inconnus sont volontairement ignorés. Cette défense en
    profondeur évite qu'un appel comme ``audio=...`` ou ``result=...`` expose
    son contenu, y compris si un futur formatter est moins restrictif.
    """
    safe_service = _validate_label(service, "service")
    safe_event = _validate_label(event, "event")
    extra: dict[str, object] = {
        "service": safe_service,
        "event": safe_event,
    }
    extra.update(
        _safe_context(
            job_uuid=job_uuid,
            attempt_count=attempt_count,
            worker_id=worker_id,
            redis_message_id=redis_message_id,
            details=details,
        )
    )
    logger.log(level, safe_event, extra=extra)


class StructuredJsonFormatter(logging.Formatter):
    """Produit une ligne JSON sans rendre le message ou l'exception arbitraire."""

    def __init__(self, *, default_service: str) -> None:
        super().__init__()
        self._default_service = _validate_label(default_service, "default_service")

    def format(self, record: logging.LogRecord) -> str:
        service = getattr(record, "service", self._default_service)
        access_context = _uvicorn_access_context(record)
        default_event = _UVICORN_ACCESS_EVENT if access_context else _UNSTRUCTURED_EVENT
        event = getattr(record, "event", default_event)
        payload: dict[str, str | int | float | bool] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "service": (
                service
                if isinstance(service, str) and service
                else self._default_service
            ),
            "event": event if isinstance(event, str) and event else _UNSTRUCTURED_EVENT,
        }
        payload.update(access_context)

        for field_name in _STRUCTURED_FIELDS:
            if not hasattr(record, field_name):
                continue
            normalized = _normalize_value(getattr(record, field_name))
            if normalized is not None:
                payload[field_name] = normalized

        if record.exc_info is not None and "error_type" not in payload:
            exception_type = record.exc_info[0]
            if exception_type is not None:
                payload["error_type"] = exception_type.__name__

        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def configure_logging(
    service: str,
    *,
    level: int = logging.INFO,
) -> None:
    """Configure le logger racine en JSON pour un point d'entrée applicatif.

    Les applications possèdent leur processus : remplacer les handlers à leur
    démarrage garantit qu'une configuration implicite de dépendance ne rétablit
    pas des logs texte susceptibles de contourner le filtrage commun.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter(default_service=service))
    logging.basicConfig(level=level, handlers=[handler], force=True)
