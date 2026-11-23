from assistant.auth import Caller
from assistant.company import Tool
from assistant.container import Services
from assistant.requests_service import RequestSummary
from assistant.tools.base import tool


@tool(Tool.MIS_SOLICITUDES)
async def mis_solicitudes(
    services: Services, caller: Caller, estado: str | None = None
) -> list[RequestSummary]:
    return await services.requests.mine(caller, estado)
