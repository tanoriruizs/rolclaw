import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import REPO

from assistant.admin.validation import validate

Break = Callable[[Path], None]


def _edit(relative: str, old: str, new: str) -> Break:
    def apply(root: Path) -> None:
        path = root / relative
        text = path.read_text(encoding="utf-8")
        assert old in text, f"{relative} ya no contiene {old!r}"
        path.write_text(text.replace(old, new), encoding="utf-8")

    return apply


def _remove(relative: str) -> Break:
    return lambda root: (root / relative).unlink()


def _orphan_agent(root: Path) -> None:
    shutil.copytree(root / "openclaw/agentes/ventas", root / "openclaw/agentes/compras")


def test_the_repository_is_valid() -> None:
    report = validate(REPO)

    assert report.problems == []
    assert report.warnings == []
    assert report.ok
    assert report.summary[0].startswith("4 roles · 6 empleados · 5 flujos")
    assert [line.split()[0] for line in report.summary[1:]] == [
        "ventas",
        "rh",
        "empleado",
        "gerente",
    ]


@pytest.mark.parametrize(
    ("damage", "expected"),
    [
        (
            _remove("openclaw/agentes/rh/SOUL.md"),
            "openclaw/agentes/rh/SOUL.md: falta la personalidad del agente del rol 'rh'",
        ),
        (
            _orphan_agent,
            "openclaw/agentes/compras: no hay ningún rol 'compras' en roles.yaml",
        ),
        (
            _edit(".env.example", "ROLE_TOKEN_GERENTE=\n", ""),
            ".env.example: falta ROLE_TOKEN_GERENTE=",
        ),
        (
            _edit(".env.example", "TELEGRAM_BOT_TOKEN_RH=\n", ""),
            ".env.example: falta TELEGRAM_BOT_TOKEN_RH=",
        ),
        (_remove(".env.example"), ".env.example: el archivo no existe"),
        (
            _edit(
                "docker-compose.yml",
                "      TELEGRAM_BOT_TOKEN_GERENTE: ${TELEGRAM_BOT_TOKEN_GERENTE:-}\n",
                "",
            ),
            "docker-compose.yml: el servicio openclaw no recibe TELEGRAM_BOT_TOKEN_GERENTE",
        ),
        (_remove("docker-compose.yml"), "docker-compose.yml: el archivo no existe"),
        (
            _edit("docker-compose.yml", "services:", "services: [roto"),
            "docker-compose.yml: YAML inválido",
        ),
        (
            _edit("openclaw/openclaw.json5", "cardinal-ventas__*", "*"),
            "openclaw/openclaw.json5: está desactualizado; ejecuta make openclaw-config",
        ),
        (
            _remove("openclaw/agentes/gerente/USER.md"),
            "openclaw/agentes/gerente/USER.md: no existe; ejecuta make openclaw-config",
        ),
        (
            _edit("empresa/empleados.yaml", "nombre: Pedro Ramírez", "nombre: Pedro Ramírez Soto"),
            "rol 'ventas': la persona no coincide con el empleado E-101",
        ),
        (
            _remove("openclaw/openclaw.plantilla.json5"),
            "openclaw: falta el archivo",
        ),
    ],
)
def test_each_inconsistency_is_reported(repo_copy: Path, damage: Break, expected: str) -> None:
    damage(repo_copy)

    report = validate(repo_copy)

    assert not report.ok
    assert [p for p in report.problems if expected in p], report.problems


def test_only_the_broken_thing_is_reported(repo_copy: Path) -> None:
    _remove("openclaw/agentes/rh/SOUL.md")(repo_copy)

    assert len(validate(repo_copy).problems) == 1


def test_compose_services_are_checked_separately(repo_copy: Path) -> None:
    _edit(
        "docker-compose.yml",
        "      DIRECTORY_TOKEN_SECRET: ${DIRECTORY_TOKEN_SECRET:-}\n"
        "      ROLE_TOKEN_VENTAS: ${ROLE_TOKEN_VENTAS:-}\n"
        "      ROLE_TOKEN_RH: ${ROLE_TOKEN_RH:-}\n",
        "      DIRECTORY_TOKEN_SECRET: ${DIRECTORY_TOKEN_SECRET:-}\n"
        "      ROLE_TOKEN_VENTAS: ${ROLE_TOKEN_VENTAS:-}\n",
    )(repo_copy)

    assert validate(repo_copy).problems == [
        "docker-compose.yml: el servicio mcp-server no recibe ROLE_TOKEN_RH"
    ]


def test_compose_without_services_is_reported(repo_copy: Path) -> None:
    (repo_copy / "docker-compose.yml").write_text("name: cardinal\n", encoding="utf-8")

    assert "docker-compose.yml: no tiene servicios" in validate(repo_copy).problems


def test_a_role_without_documents_is_only_a_warning(repo_copy: Path) -> None:
    for document in (repo_copy / "empresa/documentos").glob("*.md"):
        text = document.read_text(encoding="utf-8")
        document.write_text(
            text.replace(", rh", "").replace("rh, ", "").replace("[rh]", "[gerente]"),
            encoding="utf-8",
        )

    report = validate(repo_copy)

    assert report.ok
    assert report.warnings == ["el rol 'rh' no puede leer ningún documento"]
