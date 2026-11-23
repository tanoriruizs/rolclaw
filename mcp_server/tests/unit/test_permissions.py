import pytest

from assistant.company import Company, Tool
from assistant.errors import PermissionDeniedError
from assistant.permissions import Permissions

COMMON = {
    Tool.BUSCAR_CONOCIMIENTO,
    Tool.LISTAR_FLUJOS,
    Tool.VER_FLUJO,
    Tool.MIS_SOLICITUDES,
    Tool.CALCULAR,
}
# La matriz esperada es la especificación del plan; si roles.yaml cambia, este test lo detecta.
EXPECTED = {
    "ventas": COMMON | {Tool.INICIAR_SOLICITUD, Tool.CONSULTAR_CATALOGO},
    "rh": COMMON | {Tool.INICIAR_SOLICITUD},
    "empleado": COMMON | {Tool.INICIAR_SOLICITUD},
    "gerente": COMMON
    | {
        Tool.PENDIENTES_DE_APROBAR,
        Tool.APROBAR,
        Tool.RECHAZAR,
        Tool.VER_AUDITORIA,
        Tool.CONSULTAR_CATALOGO,
    },
}


@pytest.fixture
def permissions(company: Company) -> Permissions:
    return Permissions(company.roles)


@pytest.mark.parametrize("role", sorted(EXPECTED))
@pytest.mark.parametrize("tool", list(Tool))
def test_permission_matrix(permissions: Permissions, role: str, tool: Tool) -> None:
    allowed = tool in EXPECTED[role]

    assert permissions.is_allowed(role, tool) is allowed
    if not allowed:
        with pytest.raises(PermissionDeniedError, match="no está disponible para tu rol"):
            permissions.ensure(role, tool)


def test_unknown_role_has_no_tools(permissions: Permissions) -> None:
    assert permissions.tools_for("intruso") == frozenset()
