from assistant.auth import Caller
from assistant.company import Tool
from assistant.container import Services
from assistant.requests_service import RequestSummary
from assistant.tools.base import tool


@tool(Tool.PENDIENTES_DE_APROBAR)
async def pendientes_de_aprobar(services: Services, caller: Caller) -> list[RequestSummary]:
    return await services.requests.pending_for(caller)
