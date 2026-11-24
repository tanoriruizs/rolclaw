"""Avisos de punta a punta: la solicitud entra por REST o MCP y sale un aviso por Telegram."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from conftest import DIRECTORY_SECRET, RecordingSender
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_mcp import mcp_session

from assistant.auth import employee_token
from assistant.config import AuthMode, Settings
from assistant.errors import ConfigurationError

AuthHeaders = Callable[[str], dict[str, str]]
AppFactory = Callable[[Settings], FastAPI]
OWNER = "7000001"
BOTS = {
    role: f"100{n}:token-del-bot-de-{role}"
    for n, role in enumerate(("ventas", "rh", "empleado", "gerente"))
}
ONE_BOT = "2001:token-del-bot-unico"
QUOTE = {"cliente": "Comercial Norte", "producto_id": "LAP-001", "cantidad": 50}


@pytest.fixture
def settings(empresa_dir: Path, role_tokens: dict[str, SecretStr]) -> Settings:
    return Settings(
        empresa_dir=empresa_dir,
        role_tokens=role_tokens,
        telegram_bot_tokens={role: SecretStr(token) for role, token in BOTS.items()},
        telegram_owner_id=OWNER,
    )


def _quote(client: TestClient, headers: dict[str, str], descuento: int) -> dict[str, str]:
    body = {"flujo_id": "cotizacion_descuento", "datos": QUOTE | {"descuento_pct": descuento}}
    response = client.post("/api/v1/solicitudes", json=body, headers=headers)
    assert response.status_code == 201, response.text
    created: dict[str, str] = response.json()
    return created


def test_the_manager_is_told_when_a_request_needs_approval(
    client: TestClient, auth_headers: AuthHeaders, notices: RecordingSender
) -> None:
    created = _quote(client, auth_headers("ventas"), 15)

    assert created["estado"] == "pendiente_aprobacion"
    assert notices.sent == [
        (
            BOTS["gerente"],
            OWNER,
            "🔔 Nueva solicitud REQ0010001 de Pedro Ramírez: Cotización con descuento. "
            "Está pendiente de tu aprobación.",
        )
    ]
    # El aviso no lleva datos del trámite: ni cliente, ni montos, ni descuento.
    assert not any(word in notices.sent[0][2] for word in ("Comercial Norte", "15", "LAP"))


@pytest.mark.parametrize("descuento", [5, 30])
def test_requests_decided_by_the_rules_send_nothing(
    client: TestClient, auth_headers: AuthHeaders, notices: RecordingSender, descuento: int
) -> None:
    _quote(client, auth_headers("ventas"), descuento)

    assert notices.sent == []


@pytest.mark.parametrize(
    ("action", "body", "expected"),
    [
        (
            "aprobar",
            {"comentario": "Cliente estratégico"},
            "Tu solicitud REQ0010001 (Cotización con descuento) ✅ fue aprobada por Laura Vega. "
            "Comentario: Cliente estratégico",
        ),
        (
            "rechazar",
            {},
            "Tu solicitud REQ0010001 (Cotización con descuento) ❌ fue rechazada por Laura Vega.",
        ),
    ],
)
def test_the_requester_is_told_the_decision(
    client: TestClient,
    auth_headers: AuthHeaders,
    notices: RecordingSender,
    action: str,
    body: dict[str, str],
    expected: str,
) -> None:
    _quote(client, auth_headers("ventas"), 15)
    notices.sent.clear()

    response = client.post(
        f"/api/v1/solicitudes/REQ0010001/{action}", json=body, headers=auth_headers("gerente")
    )

    assert response.status_code == 200
    assert notices.sent == [(BOTS["ventas"], OWNER, expected)]


def test_refused_decisions_send_nothing(
    client: TestClient, auth_headers: AuthHeaders, notices: RecordingSender
) -> None:
    _quote(client, auth_headers("ventas"), 15)
    client.post("/api/v1/solicitudes/REQ0010001/aprobar", headers=auth_headers("gerente"))
    notices.sent.clear()

    forbidden = client.post(
        "/api/v1/solicitudes/REQ0010001/aprobar", headers=auth_headers("ventas")
    )
    repeated = client.post(
        "/api/v1/solicitudes/REQ0010001/rechazar", headers=auth_headers("gerente")
    )
    missing = client.post("/api/v1/solicitudes/REQ0019999/aprobar", headers=auth_headers("gerente"))

    assert (forbidden.status_code, repeated.status_code, missing.status_code) == (403, 409, 404)
    assert notices.sent == []


def test_invalid_requests_send_nothing(
    client: TestClient, auth_headers: AuthHeaders, notices: RecordingSender
) -> None:
    body = {"flujo_id": "cotizacion_descuento", "datos": QUOTE | {"descuento_pct": "mucho"}}

    response = client.post("/api/v1/solicitudes", json=body, headers=auth_headers("ventas"))

    assert response.status_code == 422
    assert notices.sent == []


def test_a_broken_telegram_never_breaks_the_request(
    client: TestClient, auth_headers: AuthHeaders, notices: RecordingSender
) -> None:
    notices.fail = True

    created = _quote(client, auth_headers("ventas"), 15)
    approved = client.post(
        "/api/v1/solicitudes/REQ0010001/aprobar", headers=auth_headers("gerente")
    )

    assert created["estado"] == "pendiente_aprobacion"
    assert approved.status_code == 200
    assert approved.json()["estado"] == "aprobada"
    # El error del envío (que lleva el token) no llega a la respuesta.
    assert not any(token in approved.text for token in BOTS.values())


async def test_requests_started_from_mcp_also_notify(
    app: FastAPI, notices: RecordingSender
) -> None:
    arguments = {"flujo_id": "cotizacion_descuento", "datos": QUOTE | {"descuento_pct": 15}}

    async with mcp_session(app, "ventas") as client:
        result = await client.call_tool("iniciar_solicitud", arguments)

    assert not result.is_error
    assert [(token, chat) for token, chat, _ in notices.sent] == [(BOTS["gerente"], OWNER)]


def test_without_telegram_configuration_nothing_is_sent(
    make_app: AppFactory,
    empresa_dir: Path,
    role_tokens: dict[str, SecretStr],
    auth_headers: AuthHeaders,
    notices: RecordingSender,
) -> None:
    plain = Settings(empresa_dir=empresa_dir, role_tokens=role_tokens)

    with TestClient(make_app(plain)) as client:
        _quote(client, auth_headers("ventas"), 15)
        client.post("/api/v1/solicitudes/REQ0010001/aprobar", headers=auth_headers("gerente"))

    assert notices.sent == []


def test_notifications_can_be_switched_off(
    make_app: AppFactory,
    settings: Settings,
    auth_headers: AuthHeaders,
    notices: RecordingSender,
) -> None:
    off = settings.model_copy(update={"notifications_enabled": False})

    with TestClient(make_app(off)) as client:
        _quote(client, auth_headers("ventas"), 15)

    assert notices.sent == []


def test_the_sender_is_closed_on_shutdown(app: FastAPI, notices: RecordingSender) -> None:
    with TestClient(app):
        assert not notices.closed

    assert notices.closed


# --- Modo directorio: un solo bot, un chat por persona -----------------------------------


def _person(employee_id: str) -> dict[str, str]:
    token = employee_token(SecretStr(DIRECTORY_SECRET), employee_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def directory_client(make_app: AppFactory, empresa_dir: Path) -> Iterator[TestClient]:
    settings = Settings(
        empresa_dir=empresa_dir,
        auth_mode=AuthMode.DIRECTORIO,
        directory_token_secret=SecretStr(DIRECTORY_SECRET),
        telegram_bot_token=SecretStr(ONE_BOT),
        telegram_users="E-001:9001,E-101:9101,E-102:9102",
    )
    with TestClient(make_app(settings)) as test_client:
        yield test_client


def test_directory_each_person_is_told_in_their_own_chat(
    directory_client: TestClient, notices: RecordingSender
) -> None:
    _quote(directory_client, _person("E-102"), 15)  # Sofía pide
    directory_client.post("/api/v1/solicitudes/REQ0010001/aprobar", headers=_person("E-001"))

    assert [(token, chat) for token, chat, _ in notices.sent] == [
        (ONE_BOT, "9001"),  # a Laura, que aprueba
        (ONE_BOT, "9102"),  # a Sofía, la decisión; a Pedro (9101) no le llega nada
    ]
    assert "de Sofía Herrera" in notices.sent[0][2]
    assert "fue aprobada por Laura Vega" in notices.sent[1][2]


def test_directory_people_without_chat_do_not_block_the_flow(
    directory_client: TestClient, notices: RecordingSender
) -> None:
    body = {
        "flujo_id": "solicitar_vacaciones",
        "datos": {"fecha_inicio": "2026-12-14", "fecha_fin": "2026-12-18"},
    }

    created = directory_client.post("/api/v1/solicitudes", json=body, headers=_person("E-302"))
    notices.sent.clear()
    decided = directory_client.post(
        f"/api/v1/solicitudes/{created.json()['numero']}/aprobar", headers=_person("E-001")
    )

    # Mariana (E-302) no dio su chat: la solicitud se aprueba igual, sin aviso para ella.
    assert (created.status_code, decided.status_code) == (201, 200)
    assert notices.sent == []


def test_unknown_people_in_telegram_users_stop_startup(
    make_app: AppFactory, empresa_dir: Path, role_tokens: dict[str, SecretStr]
) -> None:
    settings = Settings(empresa_dir=empresa_dir, role_tokens=role_tokens, telegram_users="E-999:1")

    with pytest.raises(ConfigurationError, match="E-999"):
        make_app(settings)
