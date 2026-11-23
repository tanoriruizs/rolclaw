from assistant.audit import AuditEvent
from assistant.auth import Caller
from assistant.company import Tool
from assistant.container import Services
from assistant.errors import InvalidInputError
from assistant.tools.base import tool

MAX_LIMIT = 200


@tool(Tool.VER_AUDITORIA)
async def ver_auditoria(services: Services, caller: Caller, limite: int = 50) -> list[AuditEvent]:
    if not 1 <= limite <= MAX_LIMIT:
        raise InvalidInputError(f"limite debe estar entre 1 y {MAX_LIMIT}.")
    return await services.audit.recent(limite)
