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
    created = _vacation(client, auth_headers("empleado"), "2026-12-14", "2026-12-18")
    rejected = client.post(
        f"/api/v1/solicitudes/{created.json()['numero']}/rechazar",
        json={"comentario": "Cierre de inventario esa semana"},
        headers=auth_headers("gerente"),
    )

    assert created.json()["datos"] == {
        "fecha_inicio": "2026-12-14",
        "fecha_fin": "2026-12-18",
        "dias_habiles": "5",
        "dias_por_antiguedad": "16",
        "dias_restantes": "11",
    }
    assert rejected.json()["estado"] == "rechazada"


def _vacation(client: TestClient, headers: dict[str, str], start: str, end: str) -> httpx2.Response:
    body = {
        "flujo_id": "solicitar_vacaciones",
        "datos": {"fecha_inicio": start, "fecha_fin": end},
    }
    return client.post("/api/v1/solicitudes", json=body, headers=headers)


def test_server_computes_the_quote_total(client: TestClient, auth_headers: AuthHeaders) -> None:
    datos = _quote(client, auth_headers("ventas"), 15).json()["datos"]

    assert datos["precio_unitario"] == "15000"
    assert datos["total"] == "637500.00"


def test_the_manager_sees_the_server_total(client: TestClient, auth_headers: AuthHeaders) -> None:
    _quote(client, auth_headers("ventas"), 15)

    pending = client.get("/api/v1/solicitudes/pendientes", headers=auth_headers("gerente")).json()

    assert pending[0]["datos"]["total"] == "637500.00"


@pytest.mark.parametrize(
    ("datos", "fragment"),
    [
        ({"producto_id": "LAP-999"}, "'producto_id' no existe en el catálogo"),
        ({"total": 1}, "no envíes total: lo calcula el servidor"),
    ],
)
def test_invalid_quotes_create_nothing(
    client: TestClient, auth_headers: AuthHeaders, datos: dict[str, object], fragment: str
) -> None:
    body = {
        "flujo_id": "cotizacion_descuento",
        "datos": QUOTE | {"descuento_pct": 15} | datos,
    }

    response = client.post("/api/v1/solicitudes", json=body, headers=auth_headers("ventas"))

    assert response.status_code == 422
    assert fragment in response.json()["error"]
    assert client.get("/api/v1/solicitudes", headers=auth_headers("ventas")).json() == []


def test_vacation_days_add_up_until_the_balance_runs_out(
    client: TestClient, auth_headers: AuthHeaders
) -> None:
    headers = auth_headers("empleado")  # Juan: 16 días en su año de servicio actual

    first = _vacation(client, headers, "2026-12-14", "2026-12-18").json()  # 5 días
    second = _vacation(client, headers, "2026-12-21", "2026-12-31").json()  # 8 (el 25 es festivo)
    third = _vacation(client, headers, "2027-01-04", "2027-01-08").json()  # 5: ya no alcanzan
    exact = _vacation(client, headers, "2027-01-04", "2027-01-06").json()  # 3: justo alcanzan
    extra = _vacation(client, headers, "2027-01-11", "2027-01-11").json()  # 1: ya no queda nada

    assert (first["datos"]["dias_restantes"], first["estado"]) == ("11", "pendiente_aprobacion")
    assert (second["datos"]["dias_habiles"], second["datos"]["dias_restantes"]) == ("8", "3")
    assert third["estado"] == "rechazada"
    assert third["motivo"] == "No tienes días de vacaciones suficientes para ese periodo."
    assert (exact["datos"]["dias_restantes"], exact["estado"]) == ("0", "pendiente_aprobacion")
    assert extra["estado"] == "rechazada"


def test_rejected_vacations_give_the_days_back(
    client: TestClient, auth_headers: AuthHeaders
) -> None:
    headers = auth_headers("empleado")
    first = _vacation(client, headers, "2026-12-14", "2026-12-31").json()  # 13 días

    client.post(f"/api/v1/solicitudes/{first['numero']}/rechazar", headers=auth_headers("gerente"))
    again = _vacation(client, headers, "2026-12-14", "2026-12-18").json()

    assert first["datos"]["dias_habiles"] == "13"
    assert again["datos"]["dias_restantes"] == "11"


def test_approved_vacations_keep_counting(client: TestClient, auth_headers: AuthHeaders) -> None:
    headers = auth_headers("empleado")
    first = _vacation(client, headers, "2026-12-14", "2026-12-18").json()

    client.post(f"/api/v1/solicitudes/{first['numero']}/aprobar", headers=auth_headers("gerente"))
    second = _vacation(client, headers, "2026-12-21", "2026-12-21").json()

    assert second["datos"]["dias_restantes"] == "10"


def test_each_service_year_has_its_own_balance(
    client: TestClient, auth_headers: AuthHeaders
) -> None:
    headers = auth_headers("empleado")  # su aniversario es el 14 de agosto
    _vacation(client, headers, "2026-12-14", "2026-12-18")

    before = _vacation(client, headers, "2026-08-03", "2026-08-07").json()  # año de servicio 2
    after = _vacation(client, headers, "2027-08-16", "2027-08-20").json()  # año de servicio 4

    assert (before["datos"]["dias_por_antiguedad"], before["datos"]["dias_restantes"]) == (
        "14",
        "9",
    )
    assert (after["datos"]["dias_por_antiguedad"], after["datos"]["dias_restantes"]) == ("18", "13")


def test_other_peoples_vacations_do_not_affect_my_balance(
    client: TestClient, auth_headers: AuthHeaders
) -> None:
    client.post(
        "/api/v1/solicitudes",
        json={
            "flujo_id": "registrar_vacaciones",
            "datos": {
                "empleado_id": "E-301",
                "fecha_inicio": "2026-12-01",
                "fecha_fin": "2026-12-11",
            },
        },
        headers=auth_headers("rh"),
    )

    mine = _vacation(client, auth_headers("empleado"), "2026-12-14", "2026-12-18").json()

    assert mine["datos"]["dias_restantes"] == "11"


def test_backwards_dates_return_422(client: TestClient, auth_headers: AuthHeaders) -> None:
    response = _vacation(client, auth_headers("empleado"), "2026-12-18", "2026-12-14")

    assert response.status_code == 422
    assert "la fecha final es anterior a la inicial" in response.json()["error"]


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


async def test_requests_in_an_old_format_do_not_break_the_balance(
    client: TestClient, auth_headers: AuthHeaders, servicenow: ServiceNowClient
) -> None:
    # Una solicitud guardada antes de que el servidor calculara los días (sin dias_habiles).
    await servicenow.create(
        "sc_request",
        {
            "u_flujo": "solicitar_vacaciones",
            "u_solicitante_id": "E-301",
            "u_estado": "aprobada",
            "u_datos": '{"fecha_inicio": "no-es-fecha"}',
        },
    )

    mine = _vacation(client, auth_headers("empleado"), "2026-12-14", "2026-12-18").json()

    assert mine["datos"]["dias_restantes"] == "11"
