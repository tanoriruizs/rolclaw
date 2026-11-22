"""Autenticación: token Bearer -> quién llama y con qué rol."""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import SecretStr

from assistant.company import Company
from assistant.config import ROLE_TOKEN_PREFIX
from assistant.errors import ConfigurationError

MIN_TOKEN_LENGTH = 32


@dataclass(frozen=True, slots=True)
class Caller:
    role: str
    employee_id: str
    name: str


class TokenAuthenticator:
    def __init__(self, tokens: Mapping[str, SecretStr], company: Company) -> None:
        _validate_tokens(tokens, company)
        self._entries = [
            (
                secret.get_secret_value().encode(),
                Caller(
                    role=role,
                    employee_id=company.roles[role].persona.empleado_id,
                    name=company.roles[role].persona.nombre,
                ),
            )
            for role, secret in tokens.items()
        ]

    def authenticate(self, token: str) -> Caller | None:
        # Compara contra todos los tokens en tiempo constante, sin salir antes.
        candidate = token.encode()
        found: Caller | None = None
        for expected, caller in self._entries:
            if hmac.compare_digest(candidate, expected):
                found = caller
        return found

    def authenticate_header(self, authorization: str | None) -> Caller | None:
        """Recibe el header Authorization completo ("Bearer <token>")."""
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        return self.authenticate(token.strip())


def _validate_tokens(tokens: Mapping[str, SecretStr], company: Company) -> None:
    def env_names(roles: set[str]) -> str:
        return ", ".join(f"{ROLE_TOKEN_PREFIX}{role.upper()}" for role in sorted(roles))

    missing = set(company.roles) - set(tokens)
    if missing:
        raise ConfigurationError(
            f"Faltan tokens de rol: {env_names(missing)}. Ejecuta make tokens."
        )
    unknown = set(tokens) - set(company.roles)
    if unknown:
        raise ConfigurationError(f"Hay tokens de roles que no existen: {env_names(unknown)}.")
    short = {
        role for role, secret in tokens.items() if len(secret.get_secret_value()) < MIN_TOKEN_LENGTH
    }
    if short:
        raise ConfigurationError(
            f"Tokens demasiado cortos (mínimo {MIN_TOKEN_LENGTH}): {env_names(short)}."
        )
    if len({secret.get_secret_value() for secret in tokens.values()}) != len(tokens):
        raise ConfigurationError("Dos roles no pueden compartir el mismo token.")
