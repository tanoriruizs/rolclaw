"""App factory: arma FastAPI con sus servicios, middlewares y rutas."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from assistant import __version__
from assistant.config import Settings
from assistant.container import build_services
from assistant.errors import AssistantError, AuthenticationError
from assistant.logs import RequestContextMiddleware, configure_logging
from assistant.rest import api_router, health_router

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_environ()
    configure_logging(settings.log_level)

    app = FastAPI(title="Grupo Cardinal - Asistente por roles", version=__version__)
    app.state.services = build_services(settings)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    app.include_router(api_router)

    @app.exception_handler(AssistantError)
    async def handle_assistant_error(_: Request, exc: AssistantError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, AuthenticationError) else None
        return JSONResponse({"error": exc.message}, status_code=exc.status_code, headers=headers)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        # El traceback queda en el log del servidor; el cliente solo recibe un mensaje útil.
        logger.error("error inesperado", exc_info=exc)
        return JSONResponse(
            {"error": "Error interno. Intenta de nuevo más tarde."}, status_code=500
        )

    logger.info("servidor listo", extra={"fields": {"roles": sorted(settings.role_tokens)}})
    return app
