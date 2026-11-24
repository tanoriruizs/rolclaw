"""Genera los secretos locales en .env sin imprimir sus valores."""

from __future__ import annotations

import argparse
import secrets
import shutil
import sys
from pathlib import Path

GENERATED_PREFIXES = ("ROLE_TOKEN_",)
GENERATED_KEYS = frozenset(
    {"SERVICENOW_CLIENT_SECRET", "OPENCLAW_GATEWAY_TOKEN", "DIRECTORY_TOKEN_SECRET"}
)
TOKEN_BYTES = 32  # 256 bits


def is_generated(key: str) -> bool:
    return key in GENERATED_KEYS or key.startswith(GENERATED_PREFIXES)


def fill_secrets(text: str, *, force: bool = False) -> tuple[str, list[str]]:
    """Llena los secretos vacíos y devuelve el texto nuevo y las variables tocadas."""
    output: list[str] = []
    filled: list[str] = []
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        key = key.strip()
        is_assignment = bool(separator) and not key.startswith("#")
        if is_assignment and is_generated(key) and (force or not value.strip()):
            line = f"{key}={secrets.token_urlsafe(TOKEN_BYTES)}"
            filled.append(key)
        output.append(line)
    return "\n".join(output) + "\n", filled


def add_missing(env_text: str, example_text: str) -> tuple[str, list[str]]:
    """Agrega al final las variables de la plantilla que el .env todavía no tiene."""
    present = {_key(line) for line in env_text.splitlines()} - {None}
    missing = [line for line in example_text.splitlines() if _key(line) not in present | {None}]
    if not missing:
        return env_text, []
    block = "\n# Agregadas desde .env.example\n" + "\n".join(missing) + "\n"
    return env_text.rstrip("\n") + "\n" + block, [str(_key(line)) for line in missing]


def _key(line: str) -> str | None:
    key, separator, _ = line.partition("=")
    key = key.strip()
    return key if separator and key and not key.startswith("#") else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Genera los secretos locales en .env.")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--example", type=Path, default=Path(".env.example"))
    parser.add_argument("--force", action="store_true", help="regenera aunque ya tengan valor")
    args = parser.parse_args(argv)

    env_path: Path = args.env
    if not env_path.exists():
        if not args.example.exists():
            print(f"No existe {env_path} ni {args.example}.", file=sys.stderr)
            return 1
        shutil.copyfile(args.example, env_path)
        print(f"Creado {env_path} a partir de {args.example}.")

    content = env_path.read_text(encoding="utf-8")
    if args.example.exists():
        content, added = add_missing(content, args.example.read_text(encoding="utf-8"))
        if added:
            print("Variables nuevas agregadas:", ", ".join(added))
    content, filled = fill_secrets(content, force=args.force)
    env_path.write_text(content, encoding="utf-8")
    env_path.chmod(0o600)  # solo el usuario puede leerlo

    if filled:
        print("Secretos generados:", ", ".join(filled))
    else:
        print("Nada que generar (usa --force para regenerar).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
