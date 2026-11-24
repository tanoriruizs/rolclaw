"""Modo directorio: la identidad es de la persona, no del bot."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from conftest import DIRECTORY_SECRET
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from assistant.auth import employee_token
from assistant.config import AuthMode, Settings
from assistant.errors import ConfigurationError

AppFactory = Callable[[Settings], FastAPI]
PEDRO, SOFIA, ANA, JUAN, MARIANA, LAURA = "E-101", "E-102", "E-201", "E-301", "E-302", "E-001"
QUOTE = {
    "flujo_id": "cotizacion_descuento",
    "datos": {
        "cliente": "Comercial Norte",
        "producto_id": "LAP-001",
        "cantidad": 50,
        "descuento_pct": 15,
    },
}


def _headers(employee_id: str) -> dict[str, str]:
    token = employee_token(SecretStr(DIRECTORY_SECRET), employee_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(
    make_app: AppFactory, empresa_dir: Path, role_tokens: dict[str, SecretStr]
) -> Iterator[TestClient]:
    settings = Settings(
        empresa_dir=empresa_dir,
        auth_mode=AuthMode.DIRECTORIO,
        directory_token_secret=SecretStr(DIRECTORY_SECRET),
        role_tokens=role_tokens,  # presentes en el entorno, pero no deben servir
    )
    with TestClient(make_app(settings)) as test_client:
        yield test_client


@pytest.mark.parametrize(
    ("employee_id", "name", "role"),
    [
        (PEDRO, "Pedro Ramírez", "ventas"),
        (SOFIA, "Sofía Herrera", "ventas"),
        (ANA, "Ana Torres", "rh"),
        (MARIANA, "Mariana Cruz", "empleado"),
        (LAURA, "Laura Vega", "gerente"),
    ],
)
def test_me_is_the_person_behind_the_token(
    client: TestClient, employee_id: str, name: str, role: str
) -> None:
    body = client.get("/api/v1/me", headers=_headers(employee_id)).json()

    assert (body["empleado_id"], body["nombre"], body["rol"]) == (employee_id, name, role)


def test_role_tokens_are_rejected(client: TestClient, role_tokens: dict[str, SecretStr]) -> None:
    token = role_tokens["gerente"].get_secret_value()

    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_two_salespeople_do_not_see_each_others_requests(client: TestClient) -> None:
    sofias = client.post("/api/v1/solicitudes", json=QUOTE, headers=_headers(SOFIA)).json()

    pedro_list = client.get("/api/v1/solicitudes", headers=_headers(PEDRO)).json()
    sofia_list = client.get("/api/v1/solicitudes", headers=_headers(SOFIA)).json()

    assert sofias["solicitante"] == "Sofía Herrera"
    assert pedro_list == []
    assert [r["numero"] for r in sofia_list] == [sofias["numero"]]


def test_the_manager_sees_who_really_asked(client: TestClient) -> None:
    client.post("/api/v1/solicitudes", json=QUOTE, headers=_headers(PEDRO))
    client.post("/api/v1/solicitudes", json=QUOTE, headers=_headers(SOFIA))

    pending = client.get("/api/v1/solicitudes/pendientes", headers=_headers(LAURA)).json()
    approved = client.post(
        f"/api/v1/solicitudes/{pending[1]['numero']}/aprobar", headers=_headers(LAURA)
    ).json()

    assert [p["solicitante"] for p in pending] == ["Pedro Ramírez", "Sofía Herrera"]
    assert approved["decidida_por"] == "Laura Vega"


def test_a_salesperson_cannot_approve_a_colleagues_request(client: TestClient) -> None:
    created = client.post("/api/v1/solicitudes", json=QUOTE, headers=_headers(SOFIA)).json()

    response = client.post(
        f"/api/v1/solicitudes/{created['numero']}/aprobar", headers=_headers(PEDRO)
    )

    assert response.status_code == 403


def test_vacation_balance_is_per_person(client: TestClient) -> None:
    body = {
        "flujo_id": "solicitar_vacaciones",
        "datos": {"fecha_inicio": "2026-12-14", "fecha_fin": "2026-12-18"},
    }

    juan = client.post("/api/v1/solicitudes", json=body, headers=_headers(JUAN)).json()
    mariana = client.post("/api/v1/solicitudes", json=body, headers=_headers(MARIANA)).json()

    # Juan lleva 3 años (16 días); Mariana, 1 año (12 días). Lo de Juan no le descuenta a ella.
    assert (juan["datos"]["dias_por_antiguedad"], juan["datos"]["dias_restantes"]) == ("16", "11")
    assert (mariana["datos"]["dias_por_antiguedad"], mariana["datos"]["dias_restantes"]) == (
        "12",
        "7",
    )


def test_audit_records_the_real_person(client: TestClient) -> None:
    client.post("/api/v1/solicitudes", json=QUOTE, headers=_headers(SOFIA))

    events = client.get("/api/v1/auditoria", headers=_headers(LAURA)).json()

    assert (events[-1]["empleado_id"], events[-1]["rol"]) == (SOFIA, "ventas")


def test_server_does_not_start_in_directory_mode_without_the_secret(
    make_app: AppFactory, empresa_dir: Path
) -> None:
    with pytest.raises(ConfigurationError, match="DIRECTORY_TOKEN_SECRET"):
        make_app(Settings(empresa_dir=empresa_dir, auth_mode=AuthMode.DIRECTORIO))
