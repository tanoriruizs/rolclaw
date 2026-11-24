"""Revisa .env antes de levantar los servicios y explica qué falta (sin mostrar valores)."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ALWAYS = ("ANTHROPIC_API_KEY", "SERVICENOW_CLIENT_SECRET", "OPENCLAW_GATEWAY_TOKEN")
PER_ROLE = ("ROLE_TOKEN_", "TELEGRAM_BOT_TOKEN_")
DIRECTORY = ("DIRECTORY_TOKEN_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_USERS")
LOCAL_DIR = Path("openclaw/local")
FINGERPRINT_SOURCES = (Path("empresa/empleados.yaml"), Path("empresa/roles.yaml"))
MIN_SECRET_CHARS = 32
HINTS = {
    "ANTHROPIC_API_KEY": "pega tu API key de Anthropic",
    "TELEGRAM_BOT_TOKEN": "crea el bot con @BotFather",
    "TELEGRAM_USERS": "formato E-101:111111,E-001:222222",
}


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not key or key.startswith("#"):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def fingerprint(root: Path, secret: str, telegram_users: str) -> str:
    """Huella de lo que define el modo directorio; cambia si hay que regenerarlo."""
    digest = hashlib.sha256()
    for part in (secret, telegram_users.strip()):
        digest.update(part.encode() + b"\0")
    for path in FINGERPRINT_SOURCES:
        digest.update((root / path).read_bytes() + b"\0")
    return digest.hexdigest()


def check(root: Path, env: dict[str, str], example: dict[str, str]) -> list[str]:
    """Devuelve la lista de problemas; vacía si se puede levantar."""
    problems = [_missing(key) for key in ALWAYS if not env.get(key)]
    mode = env.get("AUTH_MODE") or "demo"
    if mode == "demo":
        per_role = [key for key in example if key.startswith(PER_ROLE)]
        problems += [_missing(key) for key in per_role if not env.get(key)]
    elif mode == "directorio":
        problems += [_missing(key) for key in DIRECTORY if not env.get(key)]
        secret = env.get("DIRECTORY_TOKEN_SECRET", "")
        if secret and len(secret) < MIN_SECRET_CHARS:
            problems.append("DIRECTORY_TOKEN_SECRET es demasiado corto: ejecuta make tokens.")
        if not problems:
            problems += _check_generated(root, secret, env["TELEGRAM_USERS"])
    else:
        problems.append("AUTH_MODE debe ser demo o directorio.")
    return problems


def _check_generated(root: Path, secret: str, users: str) -> list[str]:
    stamp = root / LOCAL_DIR / "huella"
    if not stamp.is_file() or not (root / LOCAL_DIR / "openclaw.json5").is_file():
        return [f"Falta {LOCAL_DIR}/: ejecuta make directorio."]
    if stamp.read_text(encoding="utf-8").strip() != fingerprint(root, secret, users):
        return [
            "La config del directorio está desactualizada (cambió TELEGRAM_USERS, el secreto "
            "o empresa/): ejecuta make directorio."
        ]
    return []


def _missing(key: str) -> str:
    if key.startswith("TELEGRAM_BOT_TOKEN_"):
        hint = "crea el bot con @BotFather"
    else:
        hint = HINTS.get(key, "ejecuta make tokens")
    return f"Falta {key} en .env ({hint})."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Revisa .env antes de levantar los servicios.")
    parser.add_argument("--root", type=Path, default=Path())
    args = parser.parse_args(argv)
    root: Path = args.root

    try:
        env = parse_env((root / ".env").read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("No existe .env: ejecuta make tokens.", file=sys.stderr)
        return 1
    example_path = root / ".env.example"
    example = parse_env(example_path.read_text(encoding="utf-8")) if example_path.exists() else {}

    problems = check(root, env, example)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
