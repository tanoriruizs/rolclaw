from assistant.auth import Caller
from assistant.company import Tool
from assistant.container import Services
from assistant.requests_service import RequestSummary


async def mis_solicitudes(
    services: Services, caller: Caller, estado: str | None = None
) -> list[RequestSummary]:
    services.permissions.ensure(caller.role, Tool.MIS_SOLICITUDES)
    return await services.requests.mine(caller, estado)
