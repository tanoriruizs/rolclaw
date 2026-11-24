"""Línea de comandos: python -m assistant.admin <comando> (la usa el Makefile)."""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

from assistant.admin.openclaw_config import (
    LOCAL_DIR,
    OpenClawConfigError,
    demo_files,
    directory_agents,
    directory_files,
)
from assistant.admin.scaffold import DEFAULT_TOOLS, NewRole, ScaffoldError, new_role
from assistant.admin.validation import validate
from assistant.company import CompanyConfigError, Tool, load_company
from assistant.config import Settings
from assistant.errors import ConfigurationError

MIN_SECRET_CHARS = 32
NEXT_STEPS = """\
Siguientes pasos:
  1. Describe el rol en openclaw/agentes/{id}/SOUL.md y revisa sus herramientas en roles.yaml.
  2. Dale documentos (roles: [{id}] en su encabezado) y trámites en empresa/flujos/{id}/.
  3. Crea su bot con @BotFather y pon TELEGRAM_BOT_TOKEN_{upper} en .env.
  4. make tokens && make validate && make up && make pairing
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="assistant.admin", description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="raíz del repositorio")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validar", help="revisa empresa/ y lo que depende de ella")
    generate = commands.add_parser("openclaw", help="regenera la config de OpenClaw (modo demo)")
    generate.add_argument("--check", action="store_true", help="solo avisa si está desactualizada")
    commands.add_parser("directorio", help="genera la config de un solo bot (modo directorio)")
    role = commands.add_parser("nuevo-rol", help="crea un rol en todos los archivos")
    role.add_argument("id")
    role.add_argument("--nombre", required=True)
    role.add_argument("--persona", required=True, help="nombre de la persona demo del rol")
    role.add_argument("--puesto", default="Por definir")
    role.add_argument("--herramientas", default=",".join(DEFAULT_TOOLS))
    args = parser.parse_args(argv)

    try:
        if args.command == "validar":
            return _validate(args.root)
        if args.command == "openclaw":
            return _openclaw(args.root, check=args.check)
        if args.command == "directorio":
            return _directory(args.root, Settings.from_environ())
        return _new_role(args.root, args)
    except (CompanyConfigError, OpenClawConfigError, ScaffoldError, ConfigurationError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _validate(root: Path) -> int:
    report = validate(root)
    for line in report.summary:
        print(line)
    for warning in report.warnings:
        print(f"Aviso: {warning}")
    for problem in report.problems:
        print(f"Error: {problem}", file=sys.stderr)
    if report.ok:
        print("Todo en orden.")
    return 0 if report.ok else 1


def _openclaw(root: Path, *, check: bool) -> int:
    stale = _write(root, demo_files(root, load_company(root / "empresa")), dry_run=check)
    if check and stale:
        names = ", ".join(str(p) for p in stale)
        print(f"Desactualizado: {names}. Ejecuta make openclaw-config.", file=sys.stderr)
        return 1
    print("Sin cambios." if not stale else "Generado: " + ", ".join(str(p) for p in stale))
    return 0


def _directory(root: Path, settings: Settings) -> int:
    secret = settings.directory_token_secret
    if secret is None or len(secret.get_secret_value()) < MIN_SECRET_CHARS:
        raise ConfigurationError("Falta DIRECTORY_TOKEN_SECRET en .env. Ejecuta make tokens.")
    company = load_company(root / "empresa")
    chats = settings.telegram_chat_ids()
    files = directory_files(root, company, secret, chats)
    shutil.rmtree(root / LOCAL_DIR, ignore_errors=True)  # sin agentes de personas que ya no están
    _write(root, files)
    (root / LOCAL_DIR / "directorio.env").chmod(0o600)

    registered = directory_agents(company, chats)
    print(f"Modo directorio listo en {LOCAL_DIR}/ ({len(registered)} personas):")
    for agent in registered:
        print(f"  {agent.person.id}  {agent.person.nombre}  ·  {agent.role}")
    missing = [e.nombre for e in company.empleados if e.id not in chats]
    if missing:
        print("Sin Telegram registrado (no podrán usar el bot): " + ", ".join(missing))
    print("Pon AUTH_MODE=directorio en .env y ejecuta make up.")
    return 0


def _new_role(root: Path, args: argparse.Namespace) -> int:
    try:
        tools = tuple(Tool(name.strip()) for name in args.herramientas.split(",") if name.strip())
    except ValueError:
        options = ", ".join(tool.value for tool in Tool)
        raise ScaffoldError(f"herramienta desconocida. Opciones: {options}") from None
    request = NewRole(args.id, args.nombre, args.persona, args.puesto, tools)
    changed = new_role(root, request)
    print(f"Rol '{args.id}' creado. Archivos tocados:")
    for path in changed:
        print(f"  {path}")
    print(NEXT_STEPS.format(id=args.id, upper=args.id.upper()), end="")
    return 0


def _write(root: Path, files: Mapping[Path, str], *, dry_run: bool = False) -> list[Path]:
    """Escribe solo lo que cambió y devuelve esas rutas."""
    changed: list[Path] = []
    for path, content in files.items():
        target = root / path
        if target.is_file() and target.read_text(encoding="utf-8") == content:
            continue
        changed.append(path)
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    return changed


if __name__ == "__main__":
    raise SystemExit(main())
