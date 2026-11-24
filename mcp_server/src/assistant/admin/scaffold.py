"""`make new-role`: agrega un rol en todos los archivos que lo necesitan."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from assistant.admin.openclaw_config import AGENTS_DIR, LOCAL_DIR, RESERVED_AGENTS, demo_files
from assistant.admin.validation import COMPOSE, ENV_EXAMPLE, validate
from assistant.company import Tool, load_company

ROLES = Path("empresa/roles.yaml")
EMPLOYEES = Path("empresa/empleados.yaml")
ROLE_LINE_FILES = (ENV_EXAMPLE, COMPOSE)
DEFAULT_TOOLS = (
    Tool.BUSCAR_CONOCIMIENTO,
    Tool.LISTAR_FLUJOS,
    Tool.VER_FLUJO,
    Tool.INICIAR_SOLICITUD,
    Tool.MIS_SOLICITUDES,
    Tool.CALCULAR,
)
_ROLE_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_ROLE_LINE = re.compile(r"^(\s*)(ROLE_TOKEN_|TELEGRAM_BOT_TOKEN_)([A-Z0-9_]+)(\s*[:=].*)$")
_PLAIN_YAML = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 .-]*$")
_YAML_WORDS = frozenset({"null", "true", "false", "yes", "no", "on", "off"})
_EMPLOYEE_NUMBER = re.compile(r"^E-(\d+)$")


class ScaffoldError(Exception):
    """No se pudo crear el rol; el mensaje dice por qué y el repo queda como estaba."""


@dataclass(frozen=True, slots=True)
class NewRole:
    id: str
    nombre: str
    persona: str
    puesto: str = "Por definir"
    herramientas: tuple[Tool, ...] = DEFAULT_TOOLS


def new_role(root: Path, role: NewRole, *, today: date | None = None) -> list[Path]:
    """Crea el rol en una copia, la valida y solo entonces toca el repo."""
    _check_request(role)
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp)
        _copy_inputs(root, copy)
        changed = _apply(copy, role, today or date.today())
        report = validate(copy)
        if not report.ok:
            raise ScaffoldError("el rol no quedó válido: " + "; ".join(report.problems))
        for path in changed:
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(copy / path, root / path)
    return changed


def _check_request(role: NewRole) -> None:
    if not _ROLE_ID.match(role.id):
        raise ScaffoldError(
            "el id del rol debe ir en minúsculas, sin espacios ni acentos (ej. compras)"
        )
    if role.id in RESERVED_AGENTS:
        raise ScaffoldError(f"'{role.id}' es un nombre reservado")
    for label, value in (("NOMBRE", role.nombre), ("PERSONA", role.persona)):
        if not value.strip() or "\n" in value:
            raise ScaffoldError(f"falta {label} (una sola línea)")
    if not role.herramientas:
        raise ScaffoldError("el rol necesita al menos una herramienta")


def _copy_inputs(root: Path, copy: Path) -> None:
    for folder in ("empresa", "openclaw"):
        shutil.copytree(root / folder, copy / folder, ignore=shutil.ignore_patterns(LOCAL_DIR.name))
    for path in ROLE_LINE_FILES:
        if (root / path).is_file():
            (copy / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / path, copy / path)


def _apply(root: Path, role: NewRole, today: date) -> list[Path]:
    company = load_company(root / "empresa")
    if role.id in company.roles:
        raise ScaffoldError(f"el rol '{role.id}' ya existe")
    employee_id = _next_employee_id([employee.id for employee in company.empleados])
    boss = next((e.id for e in company.empleados if e.jefe is None), None)

    _append(
        root / EMPLOYEES,
        f"  - {{ id: {employee_id}, nombre: {_yaml_text(role.persona)}, "
        f"area: {_yaml_text(role.nombre)}, puesto: {_yaml_text(role.puesto)}, "
        f"rol: {role.id}, fecha_ingreso: {today.isoformat()}, jefe: {boss or 'null'} }}\n",
    )
    _append(
        root / ROLES,
        f"  {role.id}:\n"
        f"    nombre: {_yaml_text(role.nombre)}\n"
        f"    persona: {{ nombre: {_yaml_text(role.persona)}, empleado_id: {employee_id} }}\n"
        f"    herramientas: [{', '.join(role.herramientas)}]\n",
    )
    changed = [EMPLOYEES, ROLES]
    for path in ROLE_LINE_FILES:
        if (root / path).is_file():
            text = (root / path).read_text(encoding="utf-8")
            (root / path).write_text(add_role_lines(text, role.id), encoding="utf-8")
            changed.append(path)

    soul = AGENTS_DIR / role.id / "SOUL.md"
    (root / soul).parent.mkdir(parents=True, exist_ok=True)
    (root / soul).write_text(_soul(role), encoding="utf-8")
    changed.append(soul)

    for path, content in demo_files(root, load_company(root / "empresa")).items():
        if not (root / path).is_file() or (root / path).read_text(encoding="utf-8") != content:
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(content, encoding="utf-8")
            changed.append(path)
    return changed


def add_role_lines(text: str, role_id: str) -> str:
    """Repite la última línea de cada bloque ROLE_TOKEN_* / TELEGRAM_BOT_TOKEN_* para el rol."""
    new = role_id.upper()
    lines = text.splitlines()
    output: list[str] = []
    block: list[str] = []  # roles del bloque que se está leyendo
    for index, line in enumerate(lines):
        output.append(line)
        match = _ROLE_LINE.match(line)
        if match is None:
            continue
        indent, prefix, name = match.group(1, 2, 3)
        block.append(name)
        following = _ROLE_LINE.match(lines[index + 1]) if index + 1 < len(lines) else None
        if following and following.group(1, 2) == (indent, prefix):
            continue
        if new not in block:
            output.append(line.replace(prefix + name, prefix + new))
        block = []
    return "\n".join(output) + ("\n" if text.endswith("\n") else "")


def _append(path: Path, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    meaningful = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not meaningful or not meaningful[-1].startswith(" "):
        raise ScaffoldError(f"{path.name}: su lista debe ser lo último del archivo")
    path.write_text(text.rstrip("\n") + "\n" + block, encoding="utf-8")


def _next_employee_id(ids: list[str]) -> str:
    numbers = [int(m.group(1)) for m in map(_EMPLOYEE_NUMBER.match, ids) if m]
    hundred = (max(numbers, default=0) // 100 + 1) * 100
    return f"E-{hundred + 1}"


def _yaml_text(value: str) -> str:
    text = value.strip()
    if _PLAIN_YAML.match(text) and text.lower() not in _YAML_WORDS:
        return text
    return json.dumps(text, ensure_ascii=False)  # una cadena JSON también es YAML válido


def _soul(role: NewRole) -> str:
    return (
        f"# Asistente de {role.nombre.strip()}\n\n"
        f"Apoyas al área de {role.nombre.strip()}.\n\n"
        "- Responde con lo que encuentres en `buscar_conocimiento` y cita la fuente.\n"
        "- Usa `listar_flujos` para saber qué trámites puede iniciar esta persona.\n"
        "- Tono: claro y profesional.\n\n"
        "<!-- Describe aquí el trabajo del rol, sus trámites y el tono. -->\n"
    )
