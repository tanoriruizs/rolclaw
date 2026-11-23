"""Servidor MCP (Streamable HTTP): las mismas herramientas de tools/, con el rol del token."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ListToolsResult
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from assistant import __version__
from assistant.auth import Caller, TokenAuthenticator
from assistant.config import Settings
from assistant.container import Services
from assistant.errors import AssistantError, AuthenticationError
from assistant.flows import FlowDetail, FlowSummary
from assistant.rag import Fragment
from assistant.requests_service import RequestSummary
from assistant.tools.aprobar import aprobar
from assistant.tools.buscar_conocimiento import buscar_conocimiento
from assistant.tools.iniciar_solicitud import iniciar_solicitud
from assistant.tools.listar_flujos import listar_flujos
from assistant.tools.mis_solicitudes import mis_solicitudes
from assistant.tools.pendientes_de_aprobar import pendientes_de_aprobar
from assistant.tools.rechazar import rechazar
from assistant.tools.ver_flujo import ver_flujo

INSTRUCTIONS = (
    "Asistente interno de Grupo Cardinal. Solo ves las herramientas y documentos de tu rol. "
    "Antes de responder sobre políticas usa buscar_conocimiento y cita título y sección."
)


def build_mcp_server(services: Services, settings: Settings) -> MCPServer:
    server = MCPServer(
        name="grupo-cardinal",
        version=__version__,
        instructions=INSTRUCTIONS,
        middleware=[RoleToolFilter(services)],
    )

    @server.tool(name="buscar_conocimiento")
    async def _buscar_conocimiento(pregunta: str, ctx: Context, top_k: int = 4) -> list[Fragment]:
        """Busca en las políticas de la empresa que tu rol puede consultar.

        Devuelve fragmentos con titulo, seccion, texto y score. Úsala antes de responder
        cualquier pregunta sobre políticas y cita el titulo y la seccion en tu respuesta.
        top_k: cuántos fragmentos devolver (1 a 10).
        """
        return await _call(
            services, ctx, lambda c: buscar_conocimiento(services, c, pregunta, top_k)
        )

    @server.tool(name="listar_flujos")
    async def _listar_flujos(ctx: Context) -> list[FlowSummary]:
        """Lista los flujos de trabajo (trámites) que tu rol puede iniciar."""
        return await _call(services, ctx, lambda c: listar_flujos(services, c))

    @server.tool(name="ver_flujo")
    async def _ver_flujo(flujo_id: str, ctx: Context) -> FlowDetail:
        """Muestra los pasos, los datos requeridos y las reglas de un flujo de tu rol.

        Sigue los pasos en orden y pide al usuario los datos faltantes uno a la vez.
        """
        return await _call(services, ctx, lambda c: ver_flujo(services, c, flujo_id))

    @server.tool(name="iniciar_solicitud")
    async def _iniciar_solicitud(
        flujo_id: str, datos: dict[str, Any], ctx: Context
    ) -> RequestSummary:
        """Crea una solicitud de un flujo de tu rol y la registra en ServiceNow.

        datos: un objeto con los datos_requeridos del flujo (usa ver_flujo para conocerlos).
        El servidor valida los datos y aplica las reglas: la solicitud queda rechazada,
        pendiente_aprobacion o aprobada_automaticamente. Confirma con el usuario antes de usarla.
        """
        return await _call(services, ctx, lambda c: iniciar_solicitud(services, c, flujo_id, datos))

    @server.tool(name="mis_solicitudes")
    async def _mis_solicitudes(ctx: Context, estado: str | None = None) -> list[RequestSummary]:
        """Lista las solicitudes que creaste, de la más reciente a la más antigua.

        estado (opcional): pendiente_aprobacion, aprobada, aprobada_automaticamente o rechazada.
        """
        return await _call(services, ctx, lambda c: mis_solicitudes(services, c, estado))

    @server.tool(name="pendientes_de_aprobar")
    async def _pendientes_de_aprobar(ctx: Context) -> list[RequestSummary]:
        """Lista las solicitudes pendientes que le corresponde decidir a tu rol."""
        return await _call(services, ctx, lambda c: pendientes_de_aprobar(services, c))

    @server.tool(name="aprobar")
    async def _aprobar(numero: str, ctx: Context, comentario: str | None = None) -> RequestSummary:
        """Aprueba una solicitud pendiente (ej. REQ0010001). Confirma con el usuario antes."""
        return await _call(services, ctx, lambda c: aprobar(services, c, numero, comentario))

    @server.tool(name="rechazar")
    async def _rechazar(numero: str, ctx: Context, comentario: str | None = None) -> RequestSummary:
        """Rechaza una solicitud pendiente (ej. REQ0010001) con un comentario. Confirma antes."""
        return await _call(services, ctx, lambda c: rechazar(services, c, numero, comentario))

    # Crea el session manager. Sin estado entre peticiones: cada llamada trae su token.
    server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.mcp_allowed_hosts,
            allowed_origins=[f"http://{host}" for host in settings.mcp_allowed_hosts],
        ),
    )
    return server


def mcp_endpoint(server: MCPServer, auth: TokenAuthenticator) -> ASGIApp:
    return BearerGate(StreamableHTTPASGIApp(server.session_manager), auth)


async def _call[T](
    services: Services, ctx: Context, operation: Callable[[Caller], Awaitable[T]]
) -> T:
    """Re-autentica con el header de ESTA llamada y traduce errores a mensajes para el agente."""
    caller = services.auth.authenticate_header(_authorization(ctx.headers))
    if caller is None:
        raise ToolError(AuthenticationError().message)
    try:
        return await operation(caller)
    except AssistantError as exc:
        raise ToolError(exc.message) from exc


def _authorization(headers: Mapping[str, str] | None) -> str | None:
    if headers is None:
        return None
    return Headers(headers=dict(headers)).get("authorization")


class BearerGate:
    """Rechaza con 401 cualquier petición HTTP a /mcp sin un token de rol válido."""

    def __init__(self, app: ASGIApp, auth: TokenAuthenticator) -> None:
        self.app = app
        self.auth = auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        authorization = Headers(scope=scope).get("authorization")
        if scope["type"] == "http" and self.auth.authenticate_header(authorization) is None:
            response = JSONResponse(
                {"error": AuthenticationError().message},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class RoleToolFilter:
    """En tools/list cada rol solo ve sus herramientas (igual se validan al llamarlas)."""

    def __init__(self, services: Services) -> None:
        self.services = services

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        result = await call_next(ctx)
        if ctx.method != "tools/list":
            return result
        headers = getattr(ctx.request, "headers", None)
        caller = self.services.auth.authenticate_header(_authorization(headers))
        allowed = self.services.permissions.tools_for(caller.role) if caller else frozenset()
        # El SDK puede entregar el resultado como modelo o ya serializado como dict.
        if isinstance(result, ListToolsResult):
            return result.model_copy(
                update={"tools": [t for t in result.tools if t.name in allowed]}
            )
        if isinstance(result, dict):
            return {**result, "tools": [t for t in result["tools"] if t["name"] in allowed]}
        return result
