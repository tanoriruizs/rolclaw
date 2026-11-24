"""Avisos por Telegram: al aprobador cuando llega una solicitud y al solicitante cuando se decide.

Un aviso nunca hace fallar la acción: si Telegram no responde, se registra y ya.
El token del bot viaja en la URL de Telegram, así que aquí nunca se registra la URL ni el
texto de una excepción.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Protocol

import httpx2
from pydantic import SecretStr

from assistant.company import Company
from assistant.config import AuthMode, Settings
from assistant.errors import ConfigurationError

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
TIMEOUT_SECONDS = 3.0
MAX_COMMENT_CHARS = 200
_CHAT_ID = re.compile(r"^-?\d+$")


@dataclass(frozen=True, slots=True)
class Recipient:
    bot_token: SecretStr
    chat_id: str


class Sender(Protocol):
    async def send(self, recipient: Recipient, text: str) -> bool: ...

    async def aclose(self) -> None: ...


class NullSender:
    """Sin configuración de Telegram: no se envía nada."""

    async def send(self, recipient: Recipient, text: str) -> bool:
        return False

    async def aclose(self) -> None:
        return None


class TelegramSender:
    def __init__(self, http: httpx2.AsyncClient, api_base: str = TELEGRAM_API) -> None:
        self._http = http
        self._api_base = api_base.rstrip("/")

    async def send(self, recipient: Recipient, text: str) -> bool:
        url = f"{self._api_base}/bot{recipient.bot_token.get_secret_value()}/sendMessage"
        # Texto plano (sin parse_mode): lo que escriba una persona no se interpreta como formato.
        response = await self._http.post(url, json={"chat_id": recipient.chat_id, "text": text})
        if response.status_code != 200:
            # 403: la persona todavía no le ha escrito al bot; 400: el chat no existe.
            logger.warning("aviso rechazado", extra={"fields": {"status": response.status_code}})
            return False
        return True

    async def aclose(self) -> None:
        await self._http.aclose()


class Contacts:
    """A quién se le avisa y con qué bot, según el modo."""

    def __init__(self, settings: Settings, company: Company) -> None:
        employees = {employee.id: employee.rol for employee in company.empleados}
        chats = settings.telegram_chat_ids()
        unknown = sorted(set(chats) - set(employees))
        if unknown:
            raise ConfigurationError(
                f"TELEGRAM_USERS nombra empleados que no existen: {', '.join(unknown)}."
            )
        owner = settings.telegram_owner_id.strip()
        if owner and not _CHAT_ID.match(owner):
            raise ConfigurationError("TELEGRAM_OWNER_ID debe ser un id numérico de Telegram.")

        self._directory = settings.auth_mode is AuthMode.DIRECTORIO
        self._employees = employees
        self._demo_people = {
            role_id: role.persona.empleado_id for role_id, role in company.roles.items()
        }
        self._people: dict[str, Recipient] = {}
        self._role_bots: dict[str, Recipient] = {}
        if not settings.notifications_enabled:
            return
        bot = settings.telegram_bot_token
        if bot is not None and bot.get_secret_value():
            self._people = {
                employee_id: Recipient(bot, chat) for employee_id, chat in chats.items()
            }
        if owner:
            # En la demo una sola persona usa todos los bots: avisa el bot de cada rol.
            self._role_bots = {
                role: Recipient(token, owner)
                for role, token in settings.telegram_bot_tokens.items()
                if role in company.roles
            }

    @property
    def enabled(self) -> bool:
        return bool(self._people) if self._directory else bool(self._role_bots)

    def approvers(self, role: str, *, requester_id: str) -> list[Recipient]:
        """Quienes tienen el rol que aprueba, menos quien hizo la solicitud."""
        if self._directory:
            return [
                self._people[employee_id]
                for employee_id, employee_role in self._employees.items()
                if employee_role == role
                and employee_id != requester_id
                and employee_id in self._people
            ]
        if self._demo_people.get(role) == requester_id:
            return []
        return self._for_role(role)

    def requester(self, employee_id: str, role: str) -> list[Recipient]:
        if self._directory:
            return [self._people[employee_id]] if employee_id in self._people else []
        return self._for_role(role)

    def _for_role(self, role: str) -> list[Recipient]:
        return [self._role_bots[role]] if role in self._role_bots else []


@dataclass(frozen=True, slots=True)
class RequestNotice:
    """Lo mínimo de una solicitud para avisar: sin montos ni datos del trámite."""

    numero: str
    flujo_nombre: str
    solicitante: str
    solicitante_id: str
    rol_solicitante: str
    aprueba: str | None = None
    decidida_por: str | None = None
    aprobada: bool = False
    comentario: str | None = None


class RequestNotifier:
    def __init__(self, contacts: Contacts, sender: Sender) -> None:
        self._contacts = contacts
        self._sender = sender

    async def pending(self, notice: RequestNotice) -> int:
        """Avisa a quienes pueden aprobar. Devuelve cuántos avisos se entregaron."""
        if notice.aprueba is None:
            return 0
        text = (
            f"🔔 Nueva solicitud {notice.numero} de {notice.solicitante}: "
            f"{notice.flujo_nombre}. Está pendiente de tu aprobación."
        )
        recipients = self._contacts.approvers(notice.aprueba, requester_id=notice.solicitante_id)
        return await self._send_all(recipients, text, notice.numero)

    async def decided(self, notice: RequestNotice) -> int:
        """Avisa a quien hizo la solicitud."""
        verdict = "✅ fue aprobada" if notice.aprobada else "❌ fue rechazada"
        text = f"Tu solicitud {notice.numero} ({notice.flujo_nombre}) {verdict}"
        text += f" por {notice.decidida_por}." if notice.decidida_por else "."
        if notice.comentario:
            text += f" Comentario: {notice.comentario[:MAX_COMMENT_CHARS]}"
        recipients = self._contacts.requester(notice.solicitante_id, notice.rol_solicitante)
        return await self._send_all(recipients, text, notice.numero)

    async def _send_all(self, recipients: list[Recipient], text: str, number: str) -> int:
        if not recipients:
            return 0
        results = await asyncio.gather(*(self._deliver(r, text) for r in recipients))
        delivered = sum(results)
        fields = {"solicitud": number, "enviados": delivered, "destinatarios": len(recipients)}
        logger.info("avisos", extra={"fields": fields})
        return delivered

    async def _deliver(self, recipient: Recipient, text: str) -> bool:
        try:
            return await self._sender.send(recipient, text)
        except Exception as exc:
            # Solo el tipo: el mensaje de la excepción puede traer la URL con el token.
            logger.warning("aviso no enviado", extra={"fields": {"error": type(exc).__name__}})
            return False


def create_sender(contacts: Contacts) -> Sender:
    if not contacts.enabled:
        return NullSender()
    return TelegramSender(httpx2.AsyncClient(timeout=httpx2.Timeout(TIMEOUT_SECONDS)))
