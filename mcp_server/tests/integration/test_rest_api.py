from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from assistant.config import Settings
from assistant.errors import ConfigurationError
from assistant.main import create_app

AuthHeaders = Callable[[str], dict[str, str]]


def test_health_and_ready_do_not_need_a_token(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer incorrecto"}, {"Authorization": "Basic dXNlcjpwYXNz"}],
)
def test_api_requires_a_valid_bearer_token(client: TestClient, headers: dict[str, str]) -> None:
    response = client.get("/api/v1/me", headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {"error": "Token inválido o ausente."}


def test_me_returns_the_role_of_the_token(client: TestClient, auth_headers: AuthHeaders) -> None:
    body = client.get("/api/v1/me", headers=auth_headers("gerente")).json()

    assert body["rol"] == "gerente"
    assert body["nombre"] == "Laura Vega"
    assert "aprobar" in body["herramientas"]


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("ventas", {"cotizacion_descuento", "alta_cliente"}),
        ("rh", {"registrar_vacaciones"}),
        ("empleado", {"solicitar_vacaciones", "constancia_laboral"}),
        ("gerente", set()),
    ],
)
def test_each_role_only_lists_its_flows(
    client: TestClient, auth_headers: AuthHeaders, role: str, expected: set[str]
) -> None:
    flows = client.get("/api/v1/flujos", headers=auth_headers(role)).json()

    assert {flow["id"] for flow in flows} == expected


def test_view_flow_shows_readable_rules(client: TestClient, auth_headers: AuthHeaders) -> None:
    response = client.get("/api/v1/flujos/cotizacion_descuento", headers=auth_headers("ventas"))

    assert response.status_code == 200
    assert response.json()["reglas"] == [
        "Si descuento_pct es mayor que 25, se rechaza: El descuento máximo permitido es 25%.",
        "Si descuento_pct es mayor que 10, requiere aprobación de gerente.",
    ]


def test_flow_of_another_role_looks_like_it_does_not_exist(
    client: TestClient, auth_headers: AuthHeaders
) -> None:
    other_role = client.get("/api/v1/flujos/alta_cliente", headers=auth_headers("empleado"))
    missing = client.get("/api/v1/flujos/no_existe", headers=auth_headers("empleado"))

    assert other_role.status_code == missing.status_code == 404
    assert other_role.json() == {"error": "No existe el flujo 'alta_cliente' para tu rol."}


def test_tool_not_allowed_for_role_returns_403(
    empresa_copy: Path, role_tokens: dict[str, SecretStr], auth_headers: AuthHeaders
) -> None:
    roles_file = empresa_copy / "roles.yaml"
    data = yaml.safe_load(roles_file.read_text(encoding="utf-8"))
    data["roles"]["rh"]["herramientas"].remove("listar_flujos")
    roles_file.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    app = create_app(Settings(empresa_dir=empresa_copy, role_tokens=role_tokens))

    with TestClient(app) as client:
        response = client.get("/api/v1/flujos", headers=auth_headers("rh"))

    assert response.status_code == 403
    assert response.json() == {"error": "Esta acción no está disponible para tu rol."}


def test_unexpected_errors_do_not_leak_details(
    app: FastAPI,
    client: TestClient,
    auth_headers: AuthHeaders,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_: str) -> None:
        raise RuntimeError("detalle interno secreto")

    monkeypatch.setattr(app.state.services.flows, "list_for", boom)

    response = client.get("/api/v1/flujos", headers=auth_headers("ventas"))

    assert response.status_code == 500
    assert response.json() == {"error": "Error interno. Intenta de nuevo más tarde."}
    assert "secreto" not in response.text


def test_request_id_is_returned_and_validated(client: TestClient) -> None:
    echoed = client.get("/health", headers={"X-Request-ID": "mi-id-123"})
    replaced = client.get("/health", headers={"X-Request-ID": "malo\ninyectado"})

    assert echoed.headers["X-Request-ID"] == "mi-id-123"
    assert replaced.headers["X-Request-ID"] != "malo\ninyectado"
    assert len(replaced.headers["X-Request-ID"]) == 32


def test_access_log_never_contains_the_token(
    client: TestClient, auth_headers: AuthHeaders, caplog: pytest.LogCaptureFixture
) -> None:
    headers = auth_headers("ventas")
    with caplog.at_level("INFO"):
        client.get("/api/v1/me", headers=headers)

    access = [r for r in caplog.records if r.name == "assistant.http"]
    assert access
    assert access[-1].__dict__["fields"]["path"] == "/api/v1/me"
    token = headers["Authorization"].removeprefix("Bearer ")
    assert all(token not in str(r.__dict__) for r in caplog.records)


def test_server_does_not_start_without_role_tokens(empresa_dir: Path) -> None:
    with pytest.raises(ConfigurationError, match="Faltan tokens de rol"):
        create_app(Settings(empresa_dir=empresa_dir, role_tokens={}))
