"""App factory: arma FastAPI con sus servicios, middlewares y rutas."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from starlette.routing import Route

from assistant import __version__
from assistant.config import Settings
from assistant.container import Services, build_services
from assistant.errors import AssistantError, AuthenticationError
from assistant.logs import RequestContextMiddleware, configure_logging
from assistant.mcp_app import build_mcp_server, mcp_endpoint
from assistant.rag import Embedder, ensure_index
from assistant.rest import api_router, health_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    services: Services = app.state.services
    await ensure_index(services.company, services.embedder, services.store)
    async with app.state.mcp.session_manager.run():
        logger.info("servidor listo")
        yield
    await services.redis.aclose()


def create_app(
    settings: Settings | None = None,
    *,
    redis: Redis | None = None,
    embedder: Embedder | None = None,
) -> FastAPI:
    settings = settings or Settings.from_environ()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Grupo Cardinal - Asistente por roles", version=__version__, lifespan=lifespan
    )
    services = build_services(settings, redis=redis, embedder=embedder)
    app.state.services = services
    app.state.mcp = build_mcp_server(services, settings)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    app.include_router(api_router)
    app.router.routes.append(
        Route(
            "/mcp",
            mcp_endpoint(app.state.mcp, services.auth),
            methods=["GET", "POST", "DELETE"],
            include_in_schema=False,
        )
    )

    @app.exception_handler(AssistantError)
    async def handle_assistant_error(_: Request, exc: AssistantError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, AuthenticationError) else None
        return JSONResponse({"error": exc.message}, status_code=exc.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in error['loc'][1:])}: {error['msg']}"
            for error in exc.errors()
        )
        return JSONResponse({"error": f"Datos inválidos: {problems}"}, status_code=422)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        # El traceback queda en el log del servidor; el cliente solo recibe un mensaje útil.
        logger.error("error inesperado", exc_info=exc)
        return JSONResponse(
            {"error": "Error interno. Intenta de nuevo más tarde."}, status_code=500
        )

    return app
