"""Configuración del servidor a partir de variables de entorno."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from assistant.errors import ConfigurationError

ROLE_TOKEN_PREFIX = "ROLE_TOKEN_"  # noqa: S105 - es un prefijo, no un secreto
BOT_TOKEN_PREFIX = "TELEGRAM_BOT_TOKEN_"  # noqa: S105 - es un prefijo, no un secreto
_TELEGRAM_USER = re.compile(r"^([A-Za-z0-9-]+):(-?\d+)$")


class AuthMode(StrEnum):
    DEMO = "demo"  # un token (y un bot) por rol
    DIRECTORIO = "directorio"  # un token por empleado; un solo bot


class Settings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True)

    empresa_dir: Path = Path("empresa")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    auth_mode: AuthMode = AuthMode.DEMO
    role_tokens: dict[str, SecretStr] = Field(default_factory=dict)
    directory_token_secret: SecretStr | None = None
    redis_url: str = "redis://localhost:6379/0"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_cache_dir: Path | None = None
    servicenow_base_url: str = "http://localhost:8100"
    servicenow_client_id: str = "cardinal-assistant"
    servicenow_client_secret: SecretStr | None = None
    sandbox_socket: Path = Path("/run/sandbox/sandbox.sock")
    # Valores válidos del header Host en /mcp (protección contra DNS rebinding).
    mcp_allowed_hosts: list[str] = ["127.0.0.1:*", "localhost:*", "mcp-server:*"]
    # Avisos por Telegram (opcionales). Demo: un bot por rol y un solo destinatario.
    telegram_bot_tokens: dict[str, SecretStr] = Field(default_factory=dict)
    telegram_owner_id: str = ""
    # Directorio: un solo bot y un chat por empleado, "E-101:111111,E-001:222222".
    telegram_bot_token: SecretStr | None = None
    telegram_users: str = ""

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Lee ROLE_TOKEN_<ROL> y TELEGRAM_BOT_TOKEN_<ROL>: agregar un rol no requiere código."""
        env = os.environ if environ is None else environ
        return cls(
            role_tokens=_by_prefix(env, ROLE_TOKEN_PREFIX),
            telegram_bot_tokens=_by_prefix(env, BOT_TOKEN_PREFIX),
        )

    def telegram_chat_ids(self) -> dict[str, str]:
        """Empleado -> chat de Telegram, a partir de TELEGRAM_USERS."""
        chats: dict[str, str] = {}
        for item in filter(None, (part.strip() for part in self.telegram_users.split(","))):
            match = _TELEGRAM_USER.match(item)
            if match is None:
                raise ConfigurationError(
                    "TELEGRAM_USERS debe tener el formato E-101:111111,E-001:222222 "
                    f"(no se entiende '{item}')"
                )
            chats[match.group(1).upper()] = match.group(2)
        return chats


def _by_prefix(env: Mapping[str, str], prefix: str) -> dict[str, SecretStr]:
    return {
        key.removeprefix(prefix).lower(): SecretStr(value)
        for key, value in env.items()
        if key.startswith(prefix) and value
    }
