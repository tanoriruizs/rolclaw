from assistant.auth import Caller
from assistant.company import Tool
from assistant.container import Services
from assistant.requests_service import RequestSummary
from assistant.tools.base import tool


@tool(Tool.APROBAR)
async def aprobar(
    services: Services, caller: Caller, numero: str, comentario: str | None = None
) -> RequestSummary:
    return await services.requests.decide(caller, numero, approve=True, comment=comentario)
