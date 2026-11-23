"""Auditoría: cada uso de una herramienta queda en un Redis Stream (append-only).

Se guarda quién, qué herramienta, el resultado y el número de solicitud.
Nunca el contenido: ni preguntas, ni datos de solicitudes, ni tokens.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import RedisError
from redis.typing import EncodableT, FieldT

from assistant.auth import Caller
from assistant.logs import request_id_var

logger = logging.getLogger(__name__)

STREAM = "audit:events"
MAX_EVENTS = 10_000


class AuditEvent(BaseModel):
    id: str
    timestamp: str
    rol: str
    empleado_id: str
    herramienta: str
    resultado: str
    numero_solicitud: str | None = None
    request_id: str | None = None


class AuditLog:
    def __init__(self, redis: Redis, stream: str = STREAM, max_events: int = MAX_EVENTS) -> None:
        self._redis = redis
        self._stream = stream
        self._max_events = max_events

    async def record(
        self, caller: Caller, tool: str, outcome: str, request_number: str | None = None
    ) -> None:
        fields: dict[FieldT, EncodableT] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "rol": caller.role,
            "empleado_id": caller.employee_id,
            "herramienta": tool,
            "resultado": outcome,
            "numero_solicitud": request_number or "",
            "request_id": request_id_var.get() or "",
        }
        try:
            # MAXLEN aproximado: Redis recorta los eventos más viejos de forma eficiente.
            await self._redis.xadd(self._stream, fields, maxlen=self._max_events, approximate=True)
        except RedisError:
            # Si Redis falla, la acción del usuario no se bloquea, pero queda en el log.
            logger.exception(
                "no se pudo registrar el evento de auditoría", extra={"fields": fields}
            )

    async def recent(self, limit: int) -> list[AuditEvent]:
        entries = await self._redis.xrevrange(self._stream, count=limit) or []
        return [_event(entry_id, data) for entry_id, data in entries if data]


def _event(entry_id: object, data: Mapping[Any, Any]) -> AuditEvent:
    values = {_text(k): _text(v) for k, v in data.items()}
    return AuditEvent(
        id=_text(entry_id),
        timestamp=values["timestamp"],
        rol=values["rol"],
        empleado_id=values["empleado_id"],
        herramienta=values["herramienta"],
        resultado=values["resultado"],
        numero_solicitud=values.get("numero_solicitud") or None,
        request_id=values.get("request_id") or None,
    )


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
