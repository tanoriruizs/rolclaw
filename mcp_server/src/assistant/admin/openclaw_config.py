"""Genera la config de OpenClaw a partir de empresa/.

Modo demo: un agente y un bot por rol. Modo directorio: un solo bot y un agente por persona.
En los dos casos el archivo generado no lleva secretos: solo nombres de variables de entorno.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

from assistant.auth import employee_token
from assistant.company import Company
from assistant.company.models import Employee

TEMPLATE = Path("openclaw/openclaw.plantilla.json5")
DEMO_CONFIG = Path("openclaw/openclaw.json5")
AGENTS_DIR = Path("openclaw/agentes")
LOCAL_DIR = Path("openclaw/local")  # modo directorio: no se sube al repo
HEADER = (
    "// GENERADO con `make openclaw-config` a partir de openclaw.plantilla.json5 y empresa/.\n"
    "// No lo edites a mano: cambia la plantilla o empresa/ y vuelve a generarlo.\n"
)
MCP_URL = "http://mcp-server:8000/mcp"
STATE_DIR = "/home/node/.openclaw"
SYSTEM_AGENT = "sistema"
GUEST_AGENT = "invitado"
RESERVED_AGENTS = frozenset({SYSTEM_AGENT, GUEST_AGENT})
PLACEHOLDERS = ("__SERVIDORES_MCP__", "__AGENTES__", "__TELEGRAM__", "__BINDINGS__")
_PLACEHOLDER = re.compile(r"__[A-Z_]+__")
GUEST_SOUL = (
    "# Sin acceso\n\n"
    "Esta persona no está registrada en el directorio de la empresa. No tienes herramientas.\n"
    "Responde solo que no tiene acceso al asistente y que debe pedirlo al área de Sistemas.\n"
)


class OpenClawConfigError(Exception):
    """No se puede generar la config; el mensaje dice qué corregir."""


@dataclass(frozen=True, slots=True)
class Agent:
    id: str
    name: str
    token_env: str  # variable de entorno con su token del servidor MCP
    role: str
    person: Employee

    @property
    def server(self) -> str:
        return f"cardinal-{self.id}"


def role_agents(company: Company) -> list[Agent]:
    """Demo: un agente por rol, que actúa como la persona demo de ese rol."""
    people = {employee.id: employee for employee in company.empleados}
    return [
        Agent(
            id=role_id,
            name=role.nombre,
            token_env=f"ROLE_TOKEN_{role_id.upper()}",
            role=role_id,
            person=people[role.persona.empleado_id],
        )
        for role_id, role in company.roles.items()
    ]


def directory_agents(company: Company, chats: Mapping[str, str]) -> list[Agent]:
    """Directorio: un agente por cada persona que tiene Telegram registrado."""
    agents = [
        Agent(
            id=re.sub(r"[^a-z0-9]+", "-", employee.id.lower()).strip("-"),
            name=employee.nombre,
            token_env="EMPLOYEE_TOKEN_" + re.sub(r"[^A-Z0-9]+", "_", employee.id.upper()),
            role=employee.rol,
            person=employee,
        )
        for employee in company.empleados
        if employee.id in chats
    ]
    unknown = sorted(set(chats) - {employee.id for employee in company.empleados})
    if unknown:
        raise OpenClawConfigError(
            f"TELEGRAM_USERS nombra empleados que no existen: {', '.join(unknown)}."
        )
    if not agents:
        raise OpenClawConfigError("TELEGRAM_USERS está vacío: no hay a quién atender.")
    for what, values in (
        ("id de agente", [a.id for a in agents]),
        ("variable de token", [a.token_env for a in agents]),
        ("chat de Telegram", [chats[a.person.id] for a in agents]),
    ):
        repeated = sorted({value for value in values if values.count(value) > 1})
        if repeated:
            raise OpenClawConfigError(f"{what} repetido entre empleados: {', '.join(repeated)}.")
    reserved = sorted(RESERVED_AGENTS & {a.id for a in agents})
    if reserved:
        raise OpenClawConfigError(f"ids de empleado reservados: {', '.join(reserved)}.")
    groups = sorted(a.person.id for a in agents if chats[a.person.id].startswith("-"))
    if groups:
        raise OpenClawConfigError(
            f"TELEGRAM_USERS debe tener chats personales, no grupos: {', '.join(groups)}."
        )
    return agents


def _servers(agents: list[Agent]) -> dict[str, object]:
    return {
        agent.server: {
            "url": MCP_URL,
            "transport": "streamable-http",
            "headers": {"Authorization": f"Bearer ${{{agent.token_env}}}"},
        }
        for agent in agents
    }


def _toolless(name: str, agent_id: str, **extra: object) -> dict[str, object]:
    return {"name": name, "workspace": _workspace(agent_id), "tools": {"allow": []}, **extra}


def _entries(agents: list[Agent]) -> dict[str, object]:
    return {
        agent.id: {
            "name": agent.name,
            "workspace": _workspace(agent.id),
            "tools": {"allow": [f"{agent.server}__*"]},
        }
        for agent in agents
    }


def _workspace(agent_id: str) -> str:
    return f"{STATE_DIR}/workspace-{agent_id}"


def _env_secret(name: str) -> dict[str, str]:
    return {"source": "env", "provider": "default", "id": name}


def demo_blocks(company: Company) -> dict[str, object]:
    agents = role_agents(company)
    reserved = sorted(RESERVED_AGENTS & {a.id for a in agents})
    if reserved:
        raise OpenClawConfigError(f"nombres de rol reservados: {', '.join(reserved)}.")
    return {
        "__SERVIDORES_MCP__": _servers(agents),
        "__AGENTES__": {SYSTEM_AGENT: _toolless("Sistema", SYSTEM_AGENT)} | _entries(agents),
        "__TELEGRAM__": {
            "enabled": True,
            "dmPolicy": "pairing",
            "configWrites": False,
            "defaultAccount": agents[0].id,
            "accounts": {
                a.id: {"botToken": _env_secret(f"TELEGRAM_BOT_TOKEN_{a.id.upper()}")}
                for a in agents
            },
        },
        "__BINDINGS__": [
            {"agentId": a.id, "match": {"channel": "telegram", "accountId": a.id}} for a in agents
        ],
    }


def directory_blocks(company: Company, chats: Mapping[str, str]) -> dict[str, object]:
    agents = directory_agents(company, chats)
    return {
        "__SERVIDORES_MCP__": _servers(agents),
        "__AGENTES__": {
            SYSTEM_AGENT: _toolless("Sistema", SYSTEM_AGENT),
            # Si una conversación no coincide con nadie, cae aquí: un agente sin herramientas.
            GUEST_AGENT: _toolless("Sin acceso", GUEST_AGENT, default=True),
        }
        | _entries(agents),
        "__TELEGRAM__": {
            "enabled": True,
            "dmPolicy": "allowlist",
            "allowFrom": [chats[a.person.id] for a in agents],
            "groupPolicy": "disabled",
            "configWrites": False,
            "botToken": _env_secret("TELEGRAM_BOT_TOKEN"),
        },
        "__BINDINGS__": [
            {
                "agentId": a.id,
                "match": {
                    "channel": "telegram",
                    "peer": {"kind": "direct", "id": chats[a.person.id]},
                },
            }
            for a in agents
        ],
    }


def render(template: str, blocks: Mapping[str, object]) -> str:
    """Sustituye cada marcador de la plantilla por su bloque en JSON (válido en JSON5)."""
    lines: list[str] = []
    for line in template.splitlines():
        match = _PLACEHOLDER.search(line)
        if match is None:
            lines.append(line)
            continue
        if match.group() not in blocks:
            raise OpenClawConfigError(
                f"la plantilla tiene un marcador desconocido: {match.group()}"
            )
        indent = line[: len(line) - len(line.lstrip())]
        block = json.dumps(blocks[match.group()], indent=2, ensure_ascii=False)
        lines.append(line.replace(match.group(), block.replace("\n", "\n" + indent)))
    missing = [name for name in blocks if template.count(name) != 1]
    if missing:
        raise OpenClawConfigError(
            f"la plantilla debe tener una sola vez cada marcador: {', '.join(missing)}"
        )
    return HEADER + "\n".join(lines) + "\n"


def user_md(agent: Agent, role_name: str) -> str:
    person = agent.person
    return (
        "# Con quién hablas\n\n"
        f"- Nombre: {person.nombre}\n"
        f"- Puesto: {person.puesto} ({person.area})\n"
        f"- Rol en el asistente: {role_name}\n\n"
        "Llámale por su nombre. El servidor ya sabe quién es: no le pidas su número de empleado.\n"
    )


def demo_files(root: Path, company: Company) -> dict[Path, str]:
    """Archivos generados del modo demo (se guardan en el repo)."""
    template = _read(root / TEMPLATE)
    files = {DEMO_CONFIG: render(template, demo_blocks(company))}
    for agent in role_agents(company):
        files[AGENTS_DIR / agent.id / "USER.md"] = user_md(agent, company.roles[agent.role].nombre)
    return files


def directory_files(
    root: Path, company: Company, secret: SecretStr, chats: Mapping[str, str]
) -> dict[Path, str]:
    """Archivos del modo directorio: config, tokens por persona y carpeta de cada agente."""
    agents = directory_agents(company, chats)
    files = {
        LOCAL_DIR / "openclaw.json5": render(
            _read(root / TEMPLATE), directory_blocks(company, chats)
        ),
        LOCAL_DIR / "directorio.env": "".join(
            f"{agent.token_env}={employee_token(secret, agent.person.id)}\n" for agent in agents
        ),
        LOCAL_DIR / "agentes" / GUEST_AGENT / "SOUL.md": GUEST_SOUL,
    }
    for agent in agents:
        folder = LOCAL_DIR / "agentes" / agent.id
        files[folder / "SOUL.md"] = _read(root / AGENTS_DIR / agent.role / "SOUL.md")
        files[folder / "USER.md"] = user_md(agent, company.roles[agent.role].nombre)
    return files


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise OpenClawConfigError(f"falta el archivo {path}") from None
