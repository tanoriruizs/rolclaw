from collections.abc import Callable

import pytest
from fakeredis import FakeAsyncRedis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from assistant.audit import AuditLog
from assistant.auth import Caller

AuthHeaders = Callable[[str], dict[str, str]]
QUOTE = {
    "flujo_id": "cotizacion_descuento",
    "datos": {
        "cliente": "Comercial Norte",
        "producto_id": "LAP-001",
        "cantidad": 50,
        "descuento_pct": 15,
    },
}


def _audit(client: TestClient, auth_headers: AuthHeaders) -> list[dict[str, str]]:
    response = client.get("/api/v1/auditoria", headers=auth_headers("gerente"))
    assert response.status_code == 200
    events: list[dict[str, str]] = response.json()
    return events


def _summary(events: list[dict[str, str]]) -> list[tuple[str, str, str, str | None]]:
    return [
        (e["empleado_id"], e["herramienta"], e["resultado"], e["numero_solicitud"]) for e in events
    ]


def test_demo_scene_leaves_a_complete_trail(client: TestClient, auth_headers: AuthHeaders) -> None:
    client.post("/api/v1/solicitudes", json=QUOTE, headers=auth_headers("ventas"))
    client.post("/api/v1/solicitudes/REQ0010001/aprobar", headers=auth_headers("ventas"))
    client.post("/api/v1/solicitudes/REQ0010001/aprobar", headers=auth_headers("gerente"))

    events = _audit(client, auth_headers)

    assert _summary(events) == [  # del más reciente al más antiguo
        ("E-001", "aprobar", "aprobada", "REQ0010001"),
        ("E-101", "aprobar", "denegado", None),
        ("E-101", "iniciar_solicitud", "pendiente_aprobacion", "REQ0010001"),
    ]
    assert all(e["timestamp"] and e["request_id"] for e in events)


def test_consulting_the_audit_is_audited_too(client: TestClient, auth_headers: AuthHeaders) -> None:
    _audit(client, auth_headers)

    events = _audit(client, auth_headers)

    assert _summary(events)[0] == ("E-001", "ver_auditoria", "ok", None)


@pytest.mark.parametrize("role", ["ventas", "rh", "empleado"])
def test_only_gerente_can_read_the_audit_and_the_attempt_is_recorded(
    client: TestClient, auth_headers: AuthHeaders, role: str
) -> None:
    response = client.get("/api/v1/auditoria", headers=auth_headers(role))

    assert response.status_code == 403
    assert _summary(_audit(client, auth_headers))[-1][1:3] == ("ver_auditoria", "denegado")


def test_audit_never_stores_content(client: TestClient, auth_headers: AuthHeaders) -> None:
    secret_question = "pregunta-privada-de-pedro sobre su sueldo"
    client.post(
        "/api/v1/conocimiento/buscar",
        json={"pregunta": secret_question},
        headers=auth_headers("ventas"),
    )
    client.post("/api/v1/solicitudes", json=QUOTE, headers=auth_headers("ventas"))

    raw = str(_audit(client, auth_headers))

    assert "pregunta-privada" not in raw
    assert "Comercial Norte" not in raw
    assert "token" not in raw


@pytest.mark.parametrize(
    ("path", "body", "outcome"),
    [
        ("/api/v1/flujos/no_existe", None, "no_encontrado"),
        (
            "/api/v1/solicitudes",
            {"flujo_id": "cotizacion_descuento", "datos": {}},
            "datos_invalidos",
        ),
    ],
)
def test_errors_are_recorded_with_their_outcome(
    client: TestClient,
    auth_headers: AuthHeaders,
    path: str,
    body: dict[str, object] | None,
    outcome: str,
) -> None:
    if body is None:
        client.get(path, headers=auth_headers("ventas"))
    else:
        client.post(path, json=body, headers=auth_headers("ventas"))

    assert _summary(_audit(client, auth_headers))[-1][2] == outcome


def test_conflicts_and_unexpected_errors_are_recorded(
    app: FastAPI,
    client: TestClient,
    auth_headers: AuthHeaders,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client.post("/api/v1/solicitudes", json=QUOTE, headers=auth_headers("ventas"))
    client.post("/api/v1/solicitudes/REQ0010001/aprobar", headers=auth_headers("gerente"))
    client.post("/api/v1/solicitudes/REQ0010001/aprobar", headers=auth_headers("gerente"))

    def boom(_: str) -> None:
        raise RuntimeError("falla")

    monkeypatch.setattr(app.state.services.flows, "list_for", boom)
    client.get("/api/v1/flujos", headers=auth_headers("ventas"))

    outcomes = [e[2] for e in _summary(_audit(client, auth_headers))]
    assert outcomes[:2] == ["error_interno", "conflicto"]


def test_invalid_limit(client: TestClient, auth_headers: AuthHeaders) -> None:
    response = client.get(
        "/api/v1/auditoria", params={"limite": 0}, headers=auth_headers("gerente")
    )

    assert response.status_code == 422


async def test_audit_failure_does_not_break_the_action(
    fake_redis: FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def redis_down(*_: object, **__: object) -> None:
        raise RedisConnectionError("sin conexión")

    monkeypatch.setattr(fake_redis, "xadd", redis_down)
    caller = Caller(role="ventas", employee_id="E-101", name="Pedro Ramírez")

    await AuditLog(fake_redis).record(caller, "listar_flujos", "ok")

    assert "no se pudo registrar el evento de auditoría" in caplog.text
