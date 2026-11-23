"""API mínima del sandbox: POST /run y GET /health, servida por un socket Unix (sin red)."""

from __future__ import annotations

import os

from fastapi import FastAPI
from pydantic import BaseModel

from sandbox_runner.executor import ExecutionResult, Executor


class RunRequest(BaseModel):
    codigo: str


def create_app(executor: Executor | None = None) -> FastAPI:
    # Limpiar procesos sueltos solo tiene sentido (y es seguro) dentro del contenedor.
    executor = executor or Executor(kill_strays=os.environ.get("SANDBOX_KILL_STRAYS") == "1")
    app = FastAPI(title="Sandbox de cálculo", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/run")
    async def run(body: RunRequest) -> ExecutionResult:
        return await executor.run(body.codigo)

    return app
