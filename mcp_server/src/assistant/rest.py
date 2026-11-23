"""API REST: misma lógica que las herramientas MCP, distinto transporte."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from redis.exceptions import RedisError

from assistant.audit import AuditEvent
from assistant.auth import Caller
from assistant.company import Tool
from assistant.container import Services
from assistant.errors import AuthenticationError
from assistant.flows import FlowDetail, FlowSummary
from assistant.rag import Fragment
from assistant.requests_service import RequestSummary
from assistant.sandbox_client import CalculationResult
from assistant.tools.aprobar import aprobar
from assistant.tools.buscar_conocimiento import buscar_conocimiento
from assistant.tools.calcular import calcular
from assistant.tools.iniciar_solicitud import iniciar_solicitud
from assistant.tools.listar_flujos import listar_flujos
from assistant.tools.mis_solicitudes import mis_solicitudes
from assistant.tools.pendientes_de_aprobar import pendientes_de_aprobar
from assistant.tools.rechazar import rechazar
from assistant.tools.ver_auditoria import ver_auditoria
from assistant.tools.ver_flujo import ver_flujo

_bearer = HTTPBearer(auto_error=False)


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


def get_caller(
    services: ServicesDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Caller:
    caller = services.auth.authenticate(credentials.credentials) if credentials else None
    if caller is None:
        raise AuthenticationError()
    return caller


CallerDep = Annotated[Caller, Depends(get_caller)]


class Me(BaseModel):
    rol: str
    nombre: str
    empleado_id: str
    herramientas: list[Tool]


class SearchRequest(BaseModel):
    pregunta: str
    top_k: int = 4


class NewRequest(BaseModel):
    flujo_id: str
    datos: dict[str, object]


class CalculationRequest(BaseModel):
    codigo: str


class DecisionRequest(BaseModel):
    comentario: str | None = None


health_router = APIRouter(tags=["salud"])
api_router = APIRouter(prefix="/api/v1", tags=["api"])


@health_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@health_router.get("/ready")
async def ready(services: ServicesDep) -> JSONResponse:
    """Listo solo si Redis responde y el índice de documentos tiene contenido."""
    try:
        fragments = await services.store.count()
    except RedisError:
        fragments = 0
    if fragments == 0:
        return JSONResponse({"status": "not_ready"}, status_code=503)
    return JSONResponse({"status": "ready", "fragmentos": fragments})


@api_router.get("/me")
async def me(services: ServicesDep, caller: CallerDep) -> Me:
    return Me(
        rol=caller.role,
        nombre=caller.name,
        empleado_id=caller.employee_id,
        herramientas=sorted(services.permissions.tools_for(caller.role)),
    )


@api_router.get("/flujos")
async def get_flows(services: ServicesDep, caller: CallerDep) -> list[FlowSummary]:
    return await listar_flujos(services, caller)


@api_router.get("/flujos/{flujo_id}")
async def get_flow(flujo_id: str, services: ServicesDep, caller: CallerDep) -> FlowDetail:
    return await ver_flujo(services, caller, flujo_id)


@api_router.post("/conocimiento/buscar")
async def search_knowledge(
    body: SearchRequest, services: ServicesDep, caller: CallerDep
) -> list[Fragment]:
    return await buscar_conocimiento(services, caller, body.pregunta, body.top_k)


@api_router.post("/calcular")
async def calculate(
    body: CalculationRequest, services: ServicesDep, caller: CallerDep
) -> CalculationResult:
    return await calcular(services, caller, body.codigo)


@api_router.get("/auditoria")
async def audit_log(services: ServicesDep, caller: CallerDep, limite: int = 50) -> list[AuditEvent]:
    return await ver_auditoria(services, caller, limite)


@api_router.post("/solicitudes", status_code=201)
async def create_request(
    body: NewRequest, services: ServicesDep, caller: CallerDep
) -> RequestSummary:
    return await iniciar_solicitud(services, caller, body.flujo_id, body.datos)


@api_router.get("/solicitudes")
async def my_requests(
    services: ServicesDep, caller: CallerDep, estado: str | None = None
) -> list[RequestSummary]:
    return await mis_solicitudes(services, caller, estado)


@api_router.get("/solicitudes/pendientes")
async def pending_requests(services: ServicesDep, caller: CallerDep) -> list[RequestSummary]:
    return await pendientes_de_aprobar(services, caller)


@api_router.post("/solicitudes/{numero}/aprobar")
async def approve_request(
    numero: str, services: ServicesDep, caller: CallerDep, body: DecisionRequest | None = None
) -> RequestSummary:
    return await aprobar(services, caller, numero, body.comentario if body else None)


@api_router.post("/solicitudes/{numero}/rechazar")
async def reject_request(
    numero: str, services: ServicesDep, caller: CallerDep, body: DecisionRequest | None = None
) -> RequestSummary:
    return await rechazar(services, caller, numero, body.comentario if body else None)
