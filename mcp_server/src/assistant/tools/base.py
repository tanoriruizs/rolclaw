"""Decorador común de todas las herramientas: valida el permiso y registra la auditoría.

Al estar en un solo lugar, ninguna herramienta puede olvidar el permiso ni la auditoría.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Concatenate

from assistant.auth import Caller
from assistant.company import Tool
from assistant.container import Services
from assistant.errors import AssistantError
from assistant.requests_service import RequestSummary

type ToolFn[**P, R] = Callable[Concatenate[Services, Caller, P], Awaitable[R]]

_OUTCOME_BY_STATUS = {
    403: "denegado",
    404: "no_encontrado",
    409: "conflicto",
    422: "datos_invalidos",
    502: "servicio_no_disponible",
}


def tool(name: Tool) -> _ToolDecorator:
    """Uso: @tool(Tool.APROBAR) encima de la función de la herramienta."""
    return _ToolDecorator(name)


class _ToolDecorator:
    def __init__(self, name: Tool) -> None:
        self.name = name

    def __call__[**P, R](self, fn: ToolFn[P, R]) -> ToolFn[P, R]:
        name = self.name

        @functools.wraps(fn)
        async def wrapper(
            services: Services, caller: Caller, /, *args: P.args, **kwargs: P.kwargs
        ) -> R:
            try:
                services.permissions.ensure(caller.role, name)
                result = await fn(services, caller, *args, **kwargs)
            except AssistantError as exc:
                outcome = _OUTCOME_BY_STATUS.get(exc.status_code, f"error_{exc.status_code}")
                await services.audit.record(caller, name, outcome)
                raise
            except Exception:
                await services.audit.record(caller, name, "error_interno")
                raise
            if isinstance(result, RequestSummary):
                await services.audit.record(caller, name, result.estado, result.numero)
            else:
                await services.audit.record(caller, name, "ok")
            return result

        return wrapper
