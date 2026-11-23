from assistant.auth import Caller
from assistant.company import Tool
from assistant.container import Services
from assistant.flows import FlowDetail
from assistant.tools.base import tool


@tool(Tool.VER_FLUJO)
async def ver_flujo(services: Services, caller: Caller, flujo_id: str) -> FlowDetail:
    return services.flows.get_for(caller.role, flujo_id)
