"""Autenticación: token Bearer -> quién llama y con qué rol.

Dos modos:
- demo: un token por rol; quien lo usa actúa como la persona demo de ese rol.
- directorio: un token por empleado; el rol sale de empresa/empleados.yaml.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import SecretStr

from assistant.company import Company
from assistant.config import ROLE_TOKEN_PREFIX, AuthMode, Settings
from assistant.errors import ConfigurationError

MIN_TOKEN_LENGTH = 32


@dataclass(frozen=True, slots=True)
class Caller:
    role: str
    employee_id: str
    name: str


def employee_token(secret: SecretStr, employee_id: str) -> str:
    """Token de un empleado, derivado del secreto del directorio (HMAC-SHA256).

    Así basta guardar un secreto: dar de alta a alguien no requiere generar ni repartir
    un token nuevo a mano, y conocer un token no permite calcular el de otra persona.
    """
    digest = hmac.new(
        secret.get_secret_value().encode(), employee_id.encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class TokenAuthenticator:
    def __init__(self, callers_by_token: Mapping[str, Caller]) -> None:
        self._entries = [(token.encode(), caller) for token, caller in callers_by_token.items()]

    @classmethod
    def from_settings(cls, settings: Settings, company: Company) -> TokenAuthenticator:
        if settings.auth_mode is AuthMode.DIRECTORIO:
            return cls.for_directory(settings.directory_token_secret, company)
        return cls.for_roles(settings.role_tokens, company)

    @classmethod
    def for_roles(cls, tokens: Mapping[str, SecretStr], company: Company) -> TokenAuthenticator:
        _validate_role_tokens(tokens, company)
        return cls(
            {
                secret.get_secret_value(): Caller(
                    role=role,
                    employee_id=company.roles[role].persona.empleado_id,
                    name=company.roles[role].persona.nombre,
                )
                for role, secret in tokens.items()
            }
        )

    @classmethod
    def for_directory(cls, secret: SecretStr | None, company: Company) -> TokenAuthenticator:
        if secret is None or len(secret.get_secret_value()) < MIN_TOKEN_LENGTH:
            raise ConfigurationError(
                f"El modo directorio necesita DIRECTORY_TOKEN_SECRET (mínimo {MIN_TOKEN_LENGTH} "
                "caracteres). Ejecuta make tokens."
            )
        return cls(
            {
                employee_token(secret, employee.id): Caller(
                    role=employee.rol, employee_id=employee.id, name=employee.nombre
                )
                for employee in company.empleados
            }
        )

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


def _validate_role_tokens(tokens: Mapping[str, SecretStr], company: Company) -> None:
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
