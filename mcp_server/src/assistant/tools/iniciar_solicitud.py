from collections.abc import Mapping

from assistant.auth import Caller
from assistant.company import Tool
from assistant.container import Services
from assistant.requests_service import RequestSummary


async def iniciar_solicitud(
    services: Services, caller: Caller, flujo_id: str, datos: Mapping[str, object]
) -> RequestSummary:
    services.permissions.ensure(caller.role, Tool.INICIAR_SOLICITUD)
    return await services.requests.start(caller, flujo_id, datos)
