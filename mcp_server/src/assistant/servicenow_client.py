"""Cliente de la Table API de ServiceNow con OAuth 2.0 (client credentials).

Funciona igual contra el simulador local o una instancia real de desarrollador:
solo cambian la URL y las credenciales en .env.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx2
from pydantic import SecretStr

from assistant.errors import AssistantError

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
TOKEN_MARGIN_SECONDS = 60  # renueva el token un minuto antes de que expire
MAX_RETRY_AFTER_SECONDS = 10.0

Record = dict[str, str]


class ServiceNowError(AssistantError):
    status_code = 502

    def __init__(self) -> None:
        super().__init__(
            "El sistema de solicitudes no está disponible. Intenta de nuevo en unos minutos."
        )


class ServiceNowClient:
    def __init__(
        self,
        http: httpx2.AsyncClient,
        base_url: str,
        client_id: str,
        client_secret: SecretStr,
        *,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._clock = clock
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def create(self, table: str, fields: Mapping[str, str]) -> Record:
        body = await self._call("POST", f"/api/now/table/{table}", json=dict(fields))
        return _record(body["result"])

    async def update(self, table: str, sys_id: str, fields: Mapping[str, str]) -> Record:
        body = await self._call("PATCH", f"/api/now/table/{table}/{sys_id}", json=dict(fields))
        return _record(body["result"])

    async def get(self, table: str, sys_id: str) -> Record | None:
        body = await self._call("GET", f"/api/now/table/{table}/{sys_id}", allow_404=True)
        return None if body is None else _record(body["result"])

    async def query(self, table: str, query: str, limit: int = 100) -> list[Record]:
        params = {"sysparm_query": query, "sysparm_limit": str(limit)}
        body = await self._call("GET", f"/api/now/table/{table}", params=params)
        return [_record(item) for item in body["result"]]

    async def _call(self, method: str, path: str, *, allow_404: bool = False, **kwargs: Any) -> Any:
        """Reintenta con backoff exponencial ante 429, 5xx, timeouts y errores de conexión."""
        for attempt in range(1, self._max_attempts + 1):
            last_attempt = attempt == self._max_attempts
            try:
                token = await self._access_token()
                response = await self._http.request(
                    method,
                    self._base_url + path,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    **kwargs,
                )
            except httpx2.TransportError as exc:
                logger.warning(
                    "servicenow sin respuesta", extra={"fields": {"error": type(exc).__name__}}
                )
                if last_attempt:
                    raise ServiceNowError() from exc
                await self._sleep(self._delay(attempt))
                continue

            status = response.status_code
            if status == 401 and not last_attempt:
                self._token = None  # token revocado o expirado: pide uno nuevo
                continue
            if status in RETRYABLE_STATUS and not last_attempt:
                await self._sleep(self._delay(attempt, response.headers.get("Retry-After")))
                continue
            if status == 404 and allow_404:
                return None
            if response.is_error:
                logger.error(
                    "servicenow respondió con error",
                    extra={"fields": {"status": status, "method": method, "path": path}},
                )
                raise ServiceNowError()
            return response.json()
        # El último intento siempre devuelve o lanza dentro del ciclo.
        raise AssertionError("inalcanzable")  # pragma: no cover

    async def _access_token(self) -> str:
        # El lock evita que varias peticiones simultáneas pidan token al mismo tiempo.
        async with self._token_lock:
            if self._token is not None and self._clock() < self._token_expires_at:
                return self._token
            response = await self._http.post(
                self._base_url + "/oauth_token.do",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret.get_secret_value(),
                },
            )
            if response.status_code != 200:
                logger.error(
                    "servicenow rechazó las credenciales",
                    extra={"fields": {"status": response.status_code}},
                )
                raise ServiceNowError()
            payload = response.json()
            lifetime = max(int(payload.get("expires_in", 300)) - TOKEN_MARGIN_SECONDS, 0)
            self._token = str(payload["access_token"])
            self._token_expires_at = self._clock() + lifetime
            return self._token

    def _delay(self, attempt: int, retry_after: str | None = None) -> float:
        if retry_after is not None and retry_after.isdigit():
            return min(float(retry_after), MAX_RETRY_AFTER_SECONDS)
        return float(self._backoff_seconds * 2 ** (attempt - 1))


def _record(item: Any) -> Record:
    return {str(key): str(value) for key, value in dict(item).items()}
