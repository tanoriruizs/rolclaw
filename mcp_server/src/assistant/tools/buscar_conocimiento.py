from assistant.auth import Caller
from assistant.company import Tool
from assistant.container import Services
from assistant.errors import InvalidInputError
from assistant.rag import Fragment
from assistant.tools.base import tool

MAX_TOP_K = 10
MAX_QUESTION_CHARS = 500


@tool(Tool.BUSCAR_CONOCIMIENTO)
async def buscar_conocimiento(
    services: Services, caller: Caller, pregunta: str, top_k: int = 4
) -> list[Fragment]:
    if not pregunta.strip() or len(pregunta) > MAX_QUESTION_CHARS:
        raise InvalidInputError(
            f"La pregunta debe tener entre 1 y {MAX_QUESTION_CHARS} caracteres."
        )
    if not 1 <= top_k <= MAX_TOP_K:
        raise InvalidInputError(f"top_k debe estar entre 1 y {MAX_TOP_K}.")
    return await services.knowledge.search(caller.role, pregunta, top_k)
