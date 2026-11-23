"""Configuración del servidor a partir de variables de entorno."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROLE_TOKEN_PREFIX = "ROLE_TOKEN_"  # noqa: S105 - es un prefijo, no un secreto


class Settings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True)

    empresa_dir: Path = Path("empresa")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    role_tokens: dict[str, SecretStr] = Field(default_factory=dict)
    redis_url: str = "redis://localhost:6379/0"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_cache_dir: Path | None = None
    servicenow_base_url: str = "http://localhost:8100"
    servicenow_client_id: str = "cardinal-assistant"
    servicenow_client_secret: SecretStr | None = None
    sandbox_socket: Path = Path("/run/sandbox/sandbox.sock")
    # Valores válidos del header Host en /mcp (protección contra DNS rebinding).
    mcp_allowed_hosts: list[str] = ["127.0.0.1:*", "localhost:*", "mcp-server:*"]

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Lee ROLE_TOKEN_<ROL> para cada rol; así agregar un rol no requiere tocar código."""
        env = os.environ if environ is None else environ
        tokens = {
            key.removeprefix(ROLE_TOKEN_PREFIX).lower(): SecretStr(value)
            for key, value in env.items()
            if key.startswith(ROLE_TOKEN_PREFIX) and value
        }
        return cls(role_tokens=tokens)
