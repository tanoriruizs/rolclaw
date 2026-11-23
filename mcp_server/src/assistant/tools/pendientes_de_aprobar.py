from assistant.auth import Caller
from assistant.company import Tool
from assistant.container import Services
from assistant.requests_service import RequestSummary


async def pendientes_de_aprobar(services: Services, caller: Caller) -> list[RequestSummary]:
    services.permissions.ensure(caller.role, Tool.PENDIENTES_DE_APROBAR)
    return await services.requests.pending_for(caller)
