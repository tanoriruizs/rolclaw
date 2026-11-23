from assistant.auth import Caller
from assistant.company import Tool
from assistant.container import Services
from assistant.flows import FlowSummary
from assistant.tools.base import tool


@tool(Tool.LISTAR_FLUJOS)
async def listar_flujos(services: Services, caller: Caller) -> list[FlowSummary]:
    return services.flows.list_for(caller.role)
