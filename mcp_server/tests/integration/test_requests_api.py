from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx2
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from assistant.config import Settings
from assistant.servicenow_client import ServiceNowClient

AuthHeaders = Callable[[str], dict[str, str]]
AppFactory = Callable[[Settings], FastAPI]
QUOTE = {"cliente": "Comercial Norte", "producto_id": "LAP-001", "cantidad": 50}


def _quote(client: TestClient, headers: dict[str, str], descuento: object) -> httpx2.Response:
    body = {"flujo_id": "cotizacion_descuento", "datos": QUOTE | {"descuento_pct": descuento}}
    return client.post("/api/v1/solicitudes", json=body, headers=headers)


def test_demo_scene_one_end_to_end(client: TestClient, auth_headers: AuthHeaders) -> None:
    created = _quote(client, auth_headers("ventas"), 15)

    assert created.status_code == 201
    req = created.json()
    assert req["numero"] == "REQ0010001"
    assert req["estado"] == "pendiente_aprobacion"
    assert req["motivo"] == "descuento_pct (15) es mayor que 10: requiere aprobación de gerente."
    assert req["solicitante"] == "Pedro Ramírez"
    assert req["datos"]["descuento_pct"] == "15"

    pending = client.get("/api/v1/solicitudes/pendientes", headers=auth_headers("gerente"))
    assert [r["numero"] for r in pending.json()] == ["REQ0010001"]

    approved = client.post(
        "/api/v1/solicitudes/REQ0010001/aprobar",
        json={"comentario": "Cliente estratégico"},
        headers=auth_headers("gerente"),
    )
    assert approved.json()["estado"] == "aprobada"
    assert approved.json()["decidida_por"] == "Laura Vega"

    mine = client.get("/api/v1/solicitudes", headers=auth_headers("ventas")).json()
    assert mine[0]["estado"] == "aprobada"
    assert mine[0]["comentario"] == "Cliente estratégico"
    assert (
        client.get("/api/v1/solicitudes/pendientes", headers=auth_headers("gerente")).json() == []
    )


@pytest.mark.parametrize(
    ("descuento", "estado"),
    [(5, "aprobada_automaticamente"), (30, "rechazada")],
)
def test_requests_not_needing_a_decision_never_reach_the_manager(
    client: TestClient, auth_headers: AuthHeaders, descuento: int, estado: str
) -> None:
    created = _quote(client, auth_headers("ventas"), descuento).json()

    assert created["estado"] == estado
    assert (
        client.get("/api/v1/solicitudes/pendientes", headers=auth_headers("gerente")).json() == []
    )


def test_employee_vacation_request_goes_to_manager(
    client: TestClient, auth_headers: AuthHeaders
) -> None:
    body = {
        "flujo_id": "solicitar_vacaciones",
        "datos": {"fecha_inicio": "2026-12-14", "fecha_fin": "2026-12-18", "dias_habiles": 5},
    }

    created = client.post("/api/v1/solicitudes", json=body, headers=auth_headers("empleado"))
    rejected = client.post(
        f"/api/v1/solicitudes/{created.json()['numero']}/rechazar",
        json={"comentario": "Cierre de inventario esa semana"},
        headers=auth_headers("gerente"),
    )

    assert created.json()["datos"]["fecha_inicio"] == "2026-12-14"
    assert rejected.json()["estado"] == "rechazada"


def test_my_requests_are_only_mine_and_can_be_filtered(
    client: TestClient, auth_headers: AuthHeaders
) -> None:
    _quote(client, auth_headers("ventas"), 5)
    _quote(client, auth_headers("ventas"), 15)
    client.post(
        "/api/v1/solicitudes",
        json={"flujo_id": "constancia_laboral", "datos": {"dirigida_a": "Banco", "motivo": "x"}},
        headers=auth_headers("empleado"),
    )

    mine = client.get("/api/v1/solicitudes", headers=auth_headers("ventas")).json()
    pending = client.get(
        "/api/v1/solicitudes",
        params={"estado": "pendiente_aprobacion"},
        headers=auth_headers("ventas"),
    ).json()

    assert [r["numero"] for r in mine] == ["REQ0010002", "REQ0010001"]
    assert [r["numero"] for r in pending] == ["REQ0010002"]


@pytest.mark.parametrize(
    ("role", "status", "message"),
    [
        ("ventas", 403, "Esta acción no está disponible para tu rol."),
        ("empleado", 403, "Esta acción no está disponible para tu rol."),
    ],
)
def test_only_roles_with_the_tool_can_approve(
    client: TestClient, auth_headers: AuthHeaders, role: str, status: int, message: str
) -> None:
    _quote(client, auth_headers("ventas"), 15)

    response = client.post("/api/v1/solicitudes/REQ0010001/aprobar", headers=auth_headers(role))

    assert response.status_code == status
    assert response.json() == {"error": message}


@contextmanager
def _app_with_roles(
    empresa_copy: Path,
    role_tokens: dict[str, SecretStr],
    make_app: AppFactory,
    change: Callable[[dict[str, object]], None],
) -> Iterator[TestClient]:
    roles_file = empresa_copy / "roles.yaml"
    data = yaml.safe_load(roles_file.read_text(encoding="utf-8"))
    change(data)
    roles_file.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with TestClient(make_app(Settings(empresa_dir=empresa_copy, role_tokens=role_tokens))) as c:
        yield c


def test_role_with_approve_tool_cannot_decide_requests_of_another_approver(
    empresa_copy: Path,
    role_tokens: dict[str, SecretStr],
    make_app: AppFactory,
    auth_headers: AuthHeaders,
) -> None:
    def give_rh_approve(data: dict[str, object]) -> None:
        data["roles"]["rh"]["herramientas"].append("aprobar")  # type: ignore[index]

    with _app_with_roles(empresa_copy, role_tokens, make_app, give_rh_approve) as client:
        _quote(client, auth_headers("ventas"), 15)
        response = client.post("/api/v1/solicitudes/REQ0010001/aprobar", headers=auth_headers("rh"))

    assert response.status_code == 403
    assert response.json() == {
        "error": "La solicitud REQ0010001 no le corresponde decidirla a tu rol."
    }


def test_nobody_can_approve_their_own_request(
    empresa_copy: Path,
    role_tokens: dict[str, SecretStr],
    make_app: AppFactory,
    auth_headers: AuthHeaders,
) -> None:
    # La gerente también puede cotizar: su propia solicitud queda pendiente de "gerente".
    flow_file = empresa_copy / "flujos" / "ventas" / "cotizacion_descuento.yaml"
    flow_file.write_text(
        flow_file.read_text(encoding="utf-8").replace(
            "roles: [ventas]", "roles: [ventas, gerente]"
        ),
        encoding="utf-8",
    )

    def gerente_can_start(data: dict[str, object]) -> None:
        data["roles"]["gerente"]["herramientas"].append("iniciar_solicitud")  # type: ignore[index]

    with _app_with_roles(empresa_copy, role_tokens, make_app, gerente_can_start) as client:
        created = _quote(client, auth_headers("gerente"), 15).json()
        pending = client.get("/api/v1/solicitudes/pendientes", headers=auth_headers("gerente"))
        response = client.post(
            f"/api/v1/solicitudes/{created['numero']}/aprobar", headers=auth_headers("gerente")
        )

    assert created["estado"] == "pendiente_aprobacion"
    assert pending.json() == []  # las propias ni siquiera aparecen en pendientes
    assert response.status_code == 403
    assert response.json() == {"error": "No puedes aprobar ni rechazar tus propias solicitudes."}


def test_a_decided_request_cannot_be_decided_again(
    client: TestClient, auth_headers: AuthHeaders
) -> None:
    _quote(client, auth_headers("ventas"), 15)
    client.post("/api/v1/solicitudes/REQ0010001/aprobar", headers=auth_headers("gerente"))

    again = client.post("/api/v1/solicitudes/REQ0010001/rechazar", headers=auth_headers("gerente"))

    assert again.status_code == 409
    assert again.json() == {"error": "La solicitud REQ0010001 ya fue decidida (aprobada)."}


@pytest.mark.parametrize(
    ("numero", "status", "message"),
    [
        ("REQ0099999", 404, "No existe la solicitud REQ0099999."),
        ("REQ1^u_aprueba=rh", 422, "El número de solicitud debe tener el formato REQ0010001."),
    ],
)
def test_bad_request_numbers(
    client: TestClient, auth_headers: AuthHeaders, numero: str, status: int, message: str
) -> None:
    response = client.post(f"/api/v1/solicitudes/{numero}/aprobar", headers=auth_headers("gerente"))

    assert response.status_code == status
    assert response.json() == {"error": message}


@pytest.mark.parametrize(
    ("role", "body", "status", "fragment"),
    [
        ("empleado", {"flujo_id": "cotizacion_descuento", "datos": {}}, 404, "No existe el flujo"),
        ("ventas", {"flujo_id": "cotizacion_descuento", "datos": {}}, 422, "falta 'cliente'"),
        ("gerente", {"flujo_id": "cotizacion_descuento", "datos": {}}, 403, "no está disponible"),
    ],
)
def test_invalid_starts_are_explained(
    client: TestClient,
    auth_headers: AuthHeaders,
    role: str,
    body: dict[str, object],
    status: int,
    fragment: str,
) -> None:
    response = client.post("/api/v1/solicitudes", json=body, headers=auth_headers(role))

    assert response.status_code == status
    assert fragment in response.json()["error"]


def test_invalid_filter_and_long_comment_are_rejected(
    client: TestClient, auth_headers: AuthHeaders
) -> None:
    _quote(client, auth_headers("ventas"), 15)

    bad_filter = client.get(
        "/api/v1/solicitudes", params={"estado": "todas"}, headers=auth_headers("ventas")
    )
    long_comment = client.post(
        "/api/v1/solicitudes/REQ0010001/aprobar",
        json={"comentario": "x" * 501},
        headers=auth_headers("gerente"),
    )

    assert bad_filter.status_code == 422
    assert "pendiente_aprobacion" in bad_filter.json()["error"]
    assert long_comment.status_code == 422


def test_servicenow_down_returns_a_useful_502(
    app: FastAPI,
    client: TestClient,
    auth_headers: AuthHeaders,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503)

    services = app.state.services
    broken = ServiceNowClient(
        httpx2.AsyncClient(transport=httpx2.MockTransport(down)),
        "http://servicenow.test",
        "cardinal-assistant",
        SecretStr("x"),
    )
    monkeypatch.setattr(services.requests, "_servicenow", broken)

    response = client.get("/api/v1/solicitudes", headers=auth_headers("ventas"))

    assert response.status_code == 502
    assert response.json() == {
        "error": "El sistema de solicitudes no está disponible. Intenta de nuevo en unos minutos."
    }
