"""API REST: misma lógica que las herramientas MCP, distinto transporte."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from assistant.auth import Caller
from assistant.company import Tool
from assistant.container import Services
from assistant.errors import AuthenticationError
from assistant.flows import FlowDetail, FlowSummary
from assistant.tools.listar_flujos import listar_flujos
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


health_router = APIRouter(tags=["salud"])
api_router = APIRouter(prefix="/api/v1", tags=["api"])


@health_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@health_router.get("/ready")
async def ready() -> dict[str, str]:
    return {"status": "ready"}


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
