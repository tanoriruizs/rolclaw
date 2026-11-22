"""Matriz rol -> herramientas, tomada de empresa/roles.yaml."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from assistant.company import Role, Tool
from assistant.errors import PermissionDeniedError

logger = logging.getLogger(__name__)


class Permissions:
    def __init__(self, roles: Mapping[str, Role]) -> None:
        self._matrix = {role_id: role.herramientas for role_id, role in roles.items()}

    def tools_for(self, role: str) -> frozenset[Tool]:
        return self._matrix.get(role, frozenset())

    def is_allowed(self, role: str, tool: Tool) -> bool:
        return tool in self.tools_for(role)

    def ensure(self, role: str, tool: Tool) -> None:
        if not self.is_allowed(role, tool):
            logger.warning("permiso denegado", extra={"fields": {"rol": role, "herramienta": tool}})
            raise PermissionDeniedError()
