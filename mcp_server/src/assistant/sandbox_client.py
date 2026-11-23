"""Cliente del sandbox de cálculo: HTTP sobre un socket Unix (el sandbox no tiene red)."""

from __future__ import annotations

import logging

import httpx2
from pydantic import BaseModel

from assistant.errors import AssistantError

logger = logging.getLogger(__name__)


class CalculationResult(BaseModel):
    stdout: str
    resultado: str | None
    error: str | None
    duracion_ms: int
    truncado: bool


class SandboxUnavailableError(AssistantError):
    status_code = 502

    def __init__(self) -> None:
        super().__init__(
            "El servicio de cálculo no está disponible. Intenta de nuevo en unos minutos."
        )


class SandboxClient:
    """Sin reintentos: cada cálculo puede tardar hasta varios segundos y no vale repetirlo."""

    def __init__(self, http: httpx2.AsyncClient) -> None:
        self._http = http

    async def run(self, code: str) -> CalculationResult:
        try:
            response = await self._http.post("/run", json={"codigo": code})
        except httpx2.TransportError as exc:
            logger.error("sandbox sin respuesta", extra={"fields": {"error": type(exc).__name__}})
            raise SandboxUnavailableError() from exc
        if response.status_code != 200:
            logger.error(
                "sandbox respondió con error", extra={"fields": {"status": response.status_code}}
            )
            raise SandboxUnavailableError()
        return CalculationResult.model_validate(response.json())

    async def aclose(self) -> None:
        await self._http.aclose()
