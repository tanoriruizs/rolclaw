from assistant.auth import Caller
from assistant.company import Tool
from assistant.container import Services
from assistant.requests_service import RequestSummary
from assistant.tools.base import tool


@tool(Tool.RECHAZAR)
async def rechazar(
    services: Services, caller: Caller, numero: str, comentario: str | None = None
) -> RequestSummary:
    return await services.requests.decide(caller, numero, approve=False, comment=comentario)
