from assistant.auth import Caller
from assistant.company import Tool
from assistant.container import Services
from assistant.errors import InvalidInputError
from assistant.sandbox_client import CalculationResult
from assistant.tools.base import tool

MAX_CODE_CHARS = 10_000


@tool(Tool.CALCULAR)
async def calcular(services: Services, caller: Caller, codigo: str) -> CalculationResult:
    if not codigo.strip() or len(codigo) > MAX_CODE_CHARS:
        raise InvalidInputError(f"El código debe tener entre 1 y {MAX_CODE_CHARS} caracteres.")
    return await services.sandbox.run(codigo)
