from assistant.auth import Caller
from assistant.catalog import CatalogItem
from assistant.company import Tool
from assistant.container import Services
from assistant.errors import InvalidInputError
from assistant.tools.base import tool

MAX_QUERY_CHARS = 100


@tool(Tool.CONSULTAR_CATALOGO)
async def consultar_catalogo(
    services: Services, caller: Caller, busqueda: str | None = None
) -> list[CatalogItem]:
    if busqueda is not None and len(busqueda) > MAX_QUERY_CHARS:
        raise InvalidInputError(f"La búsqueda debe tener como máximo {MAX_QUERY_CHARS} caracteres.")
    return services.catalog.search(busqueda)
