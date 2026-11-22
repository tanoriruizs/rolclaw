"""Errores con mensajes pensados para el agente (nunca tracebacks)."""


class ConfigurationError(Exception):
    """La configuración es inválida; el servidor no debe arrancar."""


class AssistantError(Exception):
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AuthenticationError(AssistantError):
    status_code = 401

    def __init__(self) -> None:
        super().__init__("Token inválido o ausente.")


class PermissionDeniedError(AssistantError):
    status_code = 403

    def __init__(self) -> None:
        super().__init__("Esta acción no está disponible para tu rol.")


class NotFoundError(AssistantError):
    status_code = 404


class InvalidInputError(AssistantError):
    status_code = 422
