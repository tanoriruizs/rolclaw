from assistant.auth import Caller
from assistant.company import Tool
from assistant.container import Services
from assistant.flows import FlowDetail


async def ver_flujo(services: Services, caller: Caller, flujo_id: str) -> FlowDetail:
    services.permissions.ensure(caller.role, Tool.VER_FLUJO)
    return services.flows.get_for(caller.role, flujo_id)
