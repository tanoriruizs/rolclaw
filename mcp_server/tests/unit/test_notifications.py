import json
import logging
from dataclasses import replace

import httpx2
import pytest
from conftest import RecordingSender
from pydantic import SecretStr

from assistant.company import Company
from assistant.config import AuthMode, Settings
from assistant.errors import ConfigurationError
from assistant.logs import JsonFormatter, configure_logging
from assistant.notifications import (
    MAX_COMMENT_CHARS,
    Contacts,
    NullSender,
    Recipient,
    RequestNotice,
    RequestNotifier,
    TelegramSender,
    create_sender,
)

OWNER = "7000001"
BOTS = {
    "ventas": "1001:token-del-bot-de-ventas",
    "gerente": "1002:token-del-bot-de-gerencia",
    "empleado": "1003:token-del-bot-de-empleado",
}
ONE_BOT = "2001:token-del-bot-unico"
CHATS = "E-001:9001,E-101:9101,E-102:9102,E-301:9301"
NOTICE = RequestNotice(
    numero="REQ0010001",
    flujo_nombre="Cotización con descuento",
    solicitante="Pedro Ramírez",
    solicitante_id="E-101",
    rol_solicitante="ventas",
    aprueba="gerente",
)


def _demo(**changes: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_tokens": {role: SecretStr(token) for role, token in BOTS.items()},
        "telegram_owner_id": OWNER,
    }
    return Settings(**(values | changes))  # type: ignore[arg-type]


def _directory(**changes: object) -> Settings:
    values: dict[str, object] = {
        "auth_mode": AuthMode.DIRECTORIO,
        "telegram_bot_token": SecretStr(ONE_BOT),
        "telegram_users": CHATS,
    }
    return Settings(**(values | changes))  # type: ignore[arg-type]


def _plain(recipients: list[Recipient]) -> list[tuple[str, str]]:
    return [(r.bot_token.get_secret_value(), r.chat_id) for r in recipients]


# --- A quién se le avisa: modo demo ------------------------------------------------------


def test_demo_the_bot_of_each_role_writes_to_the_owner(company: Company) -> None:
    contacts = Contacts(_demo(), company)

    assert contacts.enabled
    assert _plain(contacts.approvers("gerente", requester_id="E-101")) == [(BOTS["gerente"], OWNER)]
    assert _plain(contacts.requester("E-101", "ventas")) == [(BOTS["ventas"], OWNER)]


def test_demo_roles_without_bot_get_nothing(company: Company) -> None:
    contacts = Contacts(_demo(), company)

    assert contacts.approvers("rh", requester_id="E-101") == []
    assert contacts.requester("E-201", "rh") == []


def test_demo_nobody_is_asked_to_approve_their_own_request(company: Company) -> None:
    contacts = Contacts(_demo(), company)

    # En la demo cada rol es una sola persona: Laura no puede aprobarse a sí misma.
    assert contacts.approvers("gerente", requester_id="E-001") == []


@pytest.mark.parametrize(
    "changes",
    [
        {"telegram_owner_id": ""},
        {"telegram_owner_id": "   "},
        {"telegram_bot_tokens": {}},
        {"notifications_enabled": False},
        {"telegram_bot_tokens": {"compras": SecretStr("1:x")}},  # rol que no existe
    ],
)
def test_demo_is_disabled_without_complete_configuration(
    company: Company, changes: dict[str, object]
) -> None:
    contacts = Contacts(_demo(**changes), company)

    assert not contacts.enabled
    assert contacts.approvers("gerente", requester_id="E-101") == []
    assert contacts.requester("E-101", "ventas") == []
    assert isinstance(create_sender(contacts), NullSender)


@pytest.mark.parametrize("owner", ["@sebastian", "70 00", "7000001;1", "abc"])
def test_owner_id_must_be_numeric(company: Company, owner: str) -> None:
    with pytest.raises(ConfigurationError, match="TELEGRAM_OWNER_ID"):
        Contacts(_demo(telegram_owner_id=owner), company)


# --- A quién se le avisa: modo directorio ------------------------------------------------


def test_directory_everyone_with_the_role_is_told_except_the_requester(company: Company) -> None:
    contacts = Contacts(_directory(), company)

    assert contacts.enabled
    assert _plain(contacts.approvers("gerente", requester_id="E-101")) == [(ONE_BOT, "9001")]
    assert _plain(contacts.approvers("ventas", requester_id="E-101")) == [(ONE_BOT, "9102")]
    assert contacts.approvers("gerente", requester_id="E-001") == []


def test_directory_the_requester_is_told_in_their_own_chat(company: Company) -> None:
    contacts = Contacts(_directory(), company)

    assert _plain(contacts.requester("E-102", "ventas")) == [(ONE_BOT, "9102")]
    assert _plain(contacts.requester("E-301", "empleado")) == [(ONE_BOT, "9301")]


def test_directory_people_without_chat_are_skipped(company: Company) -> None:
    contacts = Contacts(_directory(), company)

    assert contacts.requester("E-302", "empleado") == []  # Mariana no está en TELEGRAM_USERS
    assert contacts.approvers("rh", requester_id="E-101") == []


def test_directory_ignores_the_demo_bots(company: Company) -> None:
    demo_bots = {role: SecretStr(token) for role, token in BOTS.items()}
    contacts = Contacts(_directory(telegram_bot_tokens=demo_bots, telegram_owner_id=OWNER), company)

    recipients = contacts.approvers("gerente", requester_id="E-101") + contacts.requester(
        "E-101", "ventas"
    )

    assert {token for token, _ in _plain(recipients)} == {ONE_BOT}


@pytest.mark.parametrize(
    "changes",
    [
        {"telegram_bot_token": None},
        {"telegram_bot_token": SecretStr("")},  # compose pasa la variable vacía
        {"telegram_users": ""},
        {"notifications_enabled": False},
    ],
)
def test_directory_is_disabled_without_complete_configuration(
    company: Company, changes: dict[str, object]
) -> None:
    contacts = Contacts(_directory(**changes), company)

    assert not contacts.enabled
    assert contacts.approvers("gerente", requester_id="E-101") == []
    assert contacts.requester("E-101", "ventas") == []


def test_unknown_employees_in_telegram_users_stop_startup(company: Company) -> None:
    with pytest.raises(ConfigurationError, match="no existen: E-777, E-999"):
        Contacts(_directory(telegram_users="E-999:1,E-001:2,E-777:3"), company)


# --- Qué dice el aviso -------------------------------------------------------------------


async def test_pending_notice_names_the_request_but_not_its_data(company: Company) -> None:
    sender = RecordingSender()
    notifier = RequestNotifier(Contacts(_demo(), company), sender)

    delivered = await notifier.pending(NOTICE)

    assert delivered == 1
    assert sender.sent == [
        (
            BOTS["gerente"],
            OWNER,
            "🔔 Nueva solicitud REQ0010001 de Pedro Ramírez: Cotización con descuento. "
            "Está pendiente de tu aprobación.",
        )
    ]


async def test_requests_that_nobody_approves_send_nothing(company: Company) -> None:
    sender = RecordingSender()
    notifier = RequestNotifier(Contacts(_demo(), company), sender)

    assert await notifier.pending(replace(NOTICE, aprueba=None)) == 0
    assert sender.sent == []


@pytest.mark.parametrize(
    ("approved", "comment", "expected"),
    [
        (
            True,
            "Cliente estratégico",
            "Tu solicitud REQ0010001 (Cotización con descuento) ✅ fue aprobada por Laura Vega. "
            "Comentario: Cliente estratégico",
        ),
        (
            False,
            None,
            "Tu solicitud REQ0010001 (Cotización con descuento) ❌ fue rechazada por Laura Vega.",
        ),
    ],
)
async def test_decision_notice_goes_to_the_requester(
    company: Company, approved: bool, comment: str | None, expected: str
) -> None:
    sender = RecordingSender()
    notifier = RequestNotifier(Contacts(_demo(), company), sender)
    notice = replace(NOTICE, decidida_por="Laura Vega", aprobada=approved, comentario=comment)

    assert await notifier.decided(notice) == 1
    assert sender.sent == [(BOTS["ventas"], OWNER, expected)]


async def test_decision_without_known_approver_still_reads_well(company: Company) -> None:
    sender = RecordingSender()
    notifier = RequestNotifier(Contacts(_demo(), company), sender)

    await notifier.decided(replace(NOTICE, aprobada=True))

    assert sender.sent[0][2] == (
        "Tu solicitud REQ0010001 (Cotización con descuento) ✅ fue aprobada."
    )


async def test_long_comments_are_cut(company: Company) -> None:
    sender = RecordingSender()
    notifier = RequestNotifier(Contacts(_demo(), company), sender)

    await notifier.decided(replace(NOTICE, comentario="x" * 500))

    comment = sender.sent[0][2].split("Comentario: ")[1]
    assert comment == "x" * MAX_COMMENT_CHARS


async def test_directory_notices_reach_every_approver(company: Company) -> None:
    sender = RecordingSender()
    settings = _directory(telegram_users="E-101:9101,E-102:9102,E-001:9001")
    notifier = RequestNotifier(Contacts(settings, company), sender)

    delivered = await notifier.pending(replace(NOTICE, aprueba="ventas"))

    # Pedro (E-101) hizo la solicitud: solo le llega a Sofía.
    assert delivered == 1
    assert [(token, chat) for token, chat, _ in sender.sent] == [(ONE_BOT, "9102")]


# --- Un aviso que falla no rompe nada ----------------------------------------------------


async def test_a_failing_sender_is_swallowed_and_logged_without_secrets(
    company: Company, caplog: pytest.LogCaptureFixture
) -> None:
    sender = RecordingSender()
    sender.fail = True  # su excepción lleva el token en el mensaje
    notifier = RequestNotifier(Contacts(_demo(), company), sender)

    with caplog.at_level(logging.DEBUG):
        delivered = await notifier.pending(NOTICE)

    assert delivered == 0
    assert [r.fields for r in caplog.records if r.message == "aviso no enviado"] == [  # type: ignore[attr-defined]
        {"error": "RuntimeError"}
    ]
    assert BOTS["gerente"] not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)


async def test_one_failure_does_not_stop_the_other_notices(company: Company) -> None:
    class Flaky(RecordingSender):
        async def send(self, recipient: Recipient, text: str) -> bool:
            if recipient.chat_id == "9101":
                raise ConnectionError("sin red")
            return await super().send(recipient, text)

    sender = Flaky()
    settings = _directory(telegram_users="E-101:9101,E-102:9102,E-001:9001")
    notifier = RequestNotifier(Contacts(settings, company), sender)
    notice = replace(NOTICE, aprueba="ventas", solicitante_id="E-001")

    assert await notifier.pending(notice) == 1
    assert [chat for _, chat, _ in sender.sent] == ["9102"]


async def test_null_sender_sends_nothing() -> None:
    sender = NullSender()

    assert await sender.send(Recipient(SecretStr("1:x"), "1"), "hola") is False
    await sender.aclose()


# --- Cliente de Telegram -----------------------------------------------------------------


def _telegram(handler: httpx2.MockTransport) -> TelegramSender:
    return TelegramSender(httpx2.AsyncClient(transport=handler))


async def test_telegram_sender_calls_send_message_with_plain_text() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"ok": True})

    sender = _telegram(httpx2.MockTransport(handler))

    delivered = await sender.send(Recipient(SecretStr(ONE_BOT), "9001"), "Hola *Laura* <b>")
    await sender.aclose()

    assert delivered is True
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert str(seen[0].url) == f"https://api.telegram.org/bot{ONE_BOT}/sendMessage"
    # Sin parse_mode: el texto no se interpreta como Markdown ni HTML.
    assert json.loads(seen[0].content) == {"chat_id": "9001", "text": "Hola *Laura* <b>"}


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
async def test_telegram_rejections_are_reported_as_not_delivered(
    status: int, caplog: pytest.LogCaptureFixture
) -> None:
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(status, json={"ok": False, "description": ONE_BOT})
    )
    sender = _telegram(transport)

    with caplog.at_level(logging.DEBUG):
        delivered = await sender.send(Recipient(SecretStr(ONE_BOT), "9001"), "hola")
    await sender.aclose()

    assert delivered is False
    assert [r.fields for r in caplog.records if r.message == "aviso rechazado"] == [  # type: ignore[attr-defined]
        {"status": status}
    ]


async def test_network_errors_never_break_the_caller_nor_leak_the_token(
    company: Company, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"no se pudo conectar a {request.url}", request=request)

    sender = _telegram(httpx2.MockTransport(handler))
    notifier = RequestNotifier(Contacts(_demo(), company), sender)
    configure_logging("DEBUG")

    with caplog.at_level(logging.DEBUG):
        delivered = await notifier.pending(NOTICE)
    await sender.aclose()

    assert delivered == 0
    assert BOTS["gerente"] not in caplog.text
    assert "aviso no enviado" in caplog.text


async def test_http_client_logs_cannot_leak_the_bot_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sender = _telegram(httpx2.MockTransport(lambda _: httpx2.Response(200, json={"ok": True})))
    recipient = Recipient(SecretStr(ONE_BOT), "9001")

    # Control: sin protección, el cliente HTTP sí escribe la URL (con el token) en el log.
    with caplog.at_level(logging.DEBUG, logger="httpx2"):
        await sender.send(recipient, "hola")
    unprotected = list(caplog.records)
    assert any(ONE_BOT in record.getMessage() for record in unprotected)
    # Segunda barrera: aunque ese registro llegara al log, el formato oculta el token.
    assert all(ONE_BOT not in JsonFormatter().format(record) for record in unprotected)

    # Primera barrera: con el logging del servidor, ese registro ni siquiera se emite.
    caplog.clear()
    configure_logging("DEBUG")
    with caplog.at_level(logging.DEBUG):
        await sender.send(recipient, "hola")
    await sender.aclose()

    assert ONE_BOT not in caplog.text
    assert not [r for r in caplog.records if r.name.startswith(("httpx", "httpcore"))]


async def test_create_sender_uses_telegram_with_a_short_timeout(company: Company) -> None:
    sender = create_sender(Contacts(_demo(), company))

    assert isinstance(sender, TelegramSender)
    assert sender._http.timeout == httpx2.Timeout(3.0)
    await sender.aclose()
