from assistant.auth import Caller
from assistant.company import Tool
from assistant.container import Services
from assistant.flows import FlowSummary


async def listar_flujos(services: Services, caller: Caller) -> list[FlowSummary]:
    services.permissions.ensure(caller.role, Tool.LISTAR_FLUJOS)
    return services.flows.list_for(caller.role)
