import hashlib
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest
import yaml
from conftest import parse_json5
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from assistant.admin.openclaw_config import OpenClawConfigError
from assistant.admin.scaffold import NewRole, ScaffoldError, add_role_lines, new_role
from assistant.admin.validation import validate
from assistant.company import Tool, load_company
from assistant.config import Settings
from gen_tokens import add_missing, fill_secrets

AppFactory = Callable[[Settings], FastAPI]
COMPRAS = NewRole("compras", "Compras", "Carla Ríos", "Compradora")
TODAY = date(2026, 11, 24)


def _fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_new_role_leaves_a_valid_repository(repo_copy: Path) -> None:
    changed = new_role(repo_copy, COMPRAS, today=TODAY)

    report = validate(repo_copy)
    assert report.problems == []
    assert sorted(str(path) for path in changed) == [
        ".env.example",
        "docker-compose.yml",
        "empresa/empleados.yaml",
        "empresa/roles.yaml",
        "openclaw/agentes/compras/SOUL.md",
        "openclaw/agentes/compras/USER.md",
        "openclaw/openclaw.json5",
    ]


def test_new_role_is_added_to_the_company(repo_copy: Path) -> None:
    new_role(repo_copy, COMPRAS, today=TODAY)

    company = load_company(repo_copy / "empresa")
    role = company.roles["compras"]
    employee = company.empleados[-1]

    assert list(company.roles) == ["ventas", "rh", "empleado", "gerente", "compras"]
    assert (role.nombre, role.persona.nombre, role.persona.empleado_id) == (
        "Compras",
        "Carla Ríos",
        "E-401",
    )
    assert role.herramientas == {
        Tool.BUSCAR_CONOCIMIENTO,
        Tool.LISTAR_FLUJOS,
        Tool.VER_FLUJO,
        Tool.INICIAR_SOLICITUD,
        Tool.MIS_SOLICITUDES,
        Tool.CALCULAR,
    }
    assert (employee.id, employee.nombre, employee.rol, employee.puesto, employee.jefe) == (
        "E-401",
        "Carla Ríos",
        "compras",
        "Compradora",
        "E-001",
    )
    assert employee.fecha_ingreso == TODAY


def test_new_role_reaches_every_service(repo_copy: Path) -> None:
    new_role(repo_copy, COMPRAS, today=TODAY)

    compose = yaml.safe_load((repo_copy / "docker-compose.yml").read_text(encoding="utf-8"))
    server_env = compose["services"]["mcp-server"]["environment"]
    gateway_env = compose["services"]["openclaw"]["environment"]
    env_example = (repo_copy / ".env.example").read_text(encoding="utf-8")
    config = parse_json5((repo_copy / "openclaw/openclaw.json5").read_text(encoding="utf-8"))

    for environment in (server_env, gateway_env):
        assert environment["ROLE_TOKEN_COMPRAS"] == "${ROLE_TOKEN_COMPRAS:-}"
        assert environment["TELEGRAM_BOT_TOKEN_COMPRAS"] == "${TELEGRAM_BOT_TOKEN_COMPRAS:-}"
    assert "ROLE_TOKEN_GERENTE=\nROLE_TOKEN_COMPRAS=\n" in env_example
    assert "TELEGRAM_BOT_TOKEN_GERENTE=\nTELEGRAM_BOT_TOKEN_COMPRAS=\n" in env_example
    assert config["agents"]["entries"]["compras"] == {
        "name": "Compras",
        "workspace": "/home/node/.openclaw/workspace-compras",
        "tools": {"allow": ["cardinal-compras__*"]},
    }
    assert config["bindings"][-1] == {
        "agentId": "compras",
        "match": {"channel": "telegram", "accountId": "compras"},
    }


def test_new_role_writes_the_agent_files(repo_copy: Path) -> None:
    new_role(repo_copy, COMPRAS, today=TODAY)

    soul = (repo_copy / "openclaw/agentes/compras/SOUL.md").read_text(encoding="utf-8")
    user = (repo_copy / "openclaw/agentes/compras/USER.md").read_text(encoding="utf-8")

    assert soul.startswith("# Asistente de Compras\n")
    assert "- Nombre: Carla Ríos\n" in user
    assert "- Puesto: Compradora (Compras)\n" in user


def test_the_server_starts_with_the_new_role(
    repo_copy: Path, make_app: AppFactory, role_tokens: dict[str, SecretStr]
) -> None:
    new_role(repo_copy, COMPRAS, today=TODAY)
    token = "compras-token-de-prueba-" + "0" * 32
    settings = Settings(
        empresa_dir=repo_copy / "empresa",
        role_tokens=role_tokens | {"compras": SecretStr(token)},
    )

    with TestClient(make_app(settings)) as client:
        me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).json()
        forbidden = client.get(
            "/api/v1/solicitudes/pendientes", headers={"Authorization": f"Bearer {token}"}
        )

    assert (me["empleado_id"], me["nombre"], me["rol"]) == ("E-401", "Carla Ríos", "compras")
    assert forbidden.status_code == 403  # el rol nuevo no nace con permiso de aprobar


def test_make_tokens_generates_the_secret_of_the_new_role(repo_copy: Path) -> None:
    old_env = "ROLE_TOKEN_VENTAS=ya-existe\n"
    new_role(repo_copy, COMPRAS, today=TODAY)

    merged, added = add_missing(old_env, (repo_copy / ".env.example").read_text(encoding="utf-8"))
    _, filled = fill_secrets(merged)

    assert "ROLE_TOKEN_COMPRAS" in added
    assert "ROLE_TOKEN_COMPRAS" in filled


def test_a_second_role_gets_the_next_block_of_employee_numbers(repo_copy: Path) -> None:
    new_role(repo_copy, COMPRAS, today=TODAY)
    new_role(repo_copy, NewRole("legal", "Jurídico", "Raúl Peña"), today=TODAY)

    company = load_company(repo_copy / "empresa")

    assert company.roles["legal"].persona.empleado_id == "E-501"
    assert validate(repo_copy).ok


@pytest.mark.parametrize(
    ("nombre", "persona"),
    [
        ("Compras: nacionales", "María José O'Brien"),
        ("Compras #1", 'Ana "la jefa" Ruiz'),
        ("null", "true"),
        ("  Compras  ", "  Carla Ríos  "),
    ],
)
def test_names_with_special_characters_survive_yaml(
    repo_copy: Path, nombre: str, persona: str
) -> None:
    new_role(repo_copy, NewRole("compras", nombre, persona), today=TODAY)

    role = load_company(repo_copy / "empresa").roles["compras"]

    assert (role.nombre, role.persona.nombre) == (nombre.strip(), persona.strip())
    assert validate(repo_copy).ok


def test_custom_tools_are_respected(repo_copy: Path) -> None:
    tools = (Tool.BUSCAR_CONOCIMIENTO, Tool.CALCULAR)

    new_role(repo_copy, NewRole("compras", "Compras", "Carla Ríos", herramientas=tools))

    assert load_company(repo_copy / "empresa").roles["compras"].herramientas == set(tools)


@pytest.mark.parametrize(
    ("request_", "expected"),
    [
        (NewRole("ventas", "Ventas 2", "Otra Persona"), "el rol 'ventas' ya existe"),
        (NewRole("Compras", "Compras", "Carla Ríos"), "minúsculas"),
        (NewRole("mesa de ayuda", "Mesa", "Carla Ríos"), "minúsculas"),
        (NewRole("compras-mx", "Compras", "Carla Ríos"), "minúsculas"),
        (NewRole("", "Compras", "Carla Ríos"), "minúsculas"),
        (NewRole("sistema", "Sistema", "Carla Ríos"), "nombre reservado"),
        (NewRole("compras", " ", "Carla Ríos"), "falta NOMBRE"),
        (NewRole("compras", "Compras", ""), "falta PERSONA"),
        (NewRole("compras", "Compras", "Carla\nrol: gerente"), "falta PERSONA"),
        (NewRole("compras", "Compras", "Carla Ríos", herramientas=()), "al menos una herramienta"),
    ],
)
def test_invalid_requests_leave_the_repository_untouched(
    repo_copy: Path, request_: NewRole, expected: str
) -> None:
    before = _fingerprint(repo_copy)

    with pytest.raises(ScaffoldError, match=expected):
        new_role(repo_copy, request_)

    assert _fingerprint(repo_copy) == before


def test_a_role_that_does_not_validate_is_not_written(repo_copy: Path) -> None:
    # Sin el servicio en compose, el rol nuevo no pasaría la validación.
    compose = repo_copy / "docker-compose.yml"
    compose.write_text(
        compose.read_text(encoding="utf-8").replace("ROLE_TOKEN_", "TOKEN_DE_ROL_"),
        encoding="utf-8",
    )
    before = _fingerprint(repo_copy)

    with pytest.raises(ScaffoldError, match=r"el rol no quedó válido: .*ROLE_TOKEN_VENTAS"):
        new_role(repo_copy, COMPRAS)

    assert _fingerprint(repo_copy) == before


def test_a_missing_template_stops_before_writing(repo_copy: Path) -> None:
    (repo_copy / "openclaw/openclaw.plantilla.json5").unlink()
    before = _fingerprint(repo_copy)

    with pytest.raises(OpenClawConfigError):
        new_role(repo_copy, COMPRAS)

    assert _fingerprint(repo_copy) == before


def test_files_with_another_layout_are_not_guessed(repo_copy: Path) -> None:
    # Mismo contenido en una sola línea: es YAML válido, pero no se puede agregar al final.
    roles = repo_copy / "empresa/roles.yaml"
    data = yaml.safe_load(roles.read_text(encoding="utf-8"))
    roles.write_text(yaml.safe_dump(data, default_flow_style=True, width=10_000), encoding="utf-8")
    assert len(roles.read_text(encoding="utf-8").splitlines()) == 1
    before = _fingerprint(repo_copy)

    with pytest.raises(ScaffoldError, match=r"roles\.yaml: su lista debe ser lo último"):
        new_role(repo_copy, COMPRAS)

    assert _fingerprint(repo_copy) == before


def test_a_missing_env_example_stops_before_writing(repo_copy: Path) -> None:
    (repo_copy / ".env.example").unlink()
    before = _fingerprint(repo_copy)

    with pytest.raises(ScaffoldError, match=r"\.env\.example: el archivo no existe"):
        new_role(repo_copy, COMPRAS)

    assert _fingerprint(repo_copy) == before


# --- Líneas por rol en .env.example, docker-compose.yml y ci.yml -------------------------


def test_role_lines_are_cloned_at_the_end_of_each_block() -> None:
    text = (
        "A=1\n"
        "ROLE_TOKEN_VENTAS=\n"
        "ROLE_TOKEN_RH=\n"
        "# comentario\n"
        "      ROLE_TOKEN_VENTAS: ${ROLE_TOKEN_VENTAS:?x}\n"
        "      ROLE_TOKEN_RH: ${ROLE_TOKEN_RH:?x}\n"
        "      TELEGRAM_BOT_TOKEN_RH: ${TELEGRAM_BOT_TOKEN_RH:-}\n"
        "TELEGRAM_BOT_TOKEN=\n"
    )

    assert add_role_lines(text, "compras") == (
        "A=1\n"
        "ROLE_TOKEN_VENTAS=\n"
        "ROLE_TOKEN_RH=\n"
        "ROLE_TOKEN_COMPRAS=\n"
        "# comentario\n"
        "      ROLE_TOKEN_VENTAS: ${ROLE_TOKEN_VENTAS:?x}\n"
        "      ROLE_TOKEN_RH: ${ROLE_TOKEN_RH:?x}\n"
        "      ROLE_TOKEN_COMPRAS: ${ROLE_TOKEN_COMPRAS:?x}\n"
        "      TELEGRAM_BOT_TOKEN_RH: ${TELEGRAM_BOT_TOKEN_RH:-}\n"
        "      TELEGRAM_BOT_TOKEN_COMPRAS: ${TELEGRAM_BOT_TOKEN_COMPRAS:-}\n"
        "TELEGRAM_BOT_TOKEN=\n"  # el bot único del modo directorio no es de ningún rol
    )


def test_role_lines_are_not_duplicated() -> None:
    once = add_role_lines("ROLE_TOKEN_RH=\n", "compras")

    assert add_role_lines(once, "compras") == once == "ROLE_TOKEN_RH=\nROLE_TOKEN_COMPRAS=\n"


@pytest.mark.parametrize("text", ["ROLE_TOKEN_RH=", "sin variables\n", ""])
def test_role_lines_keep_the_end_of_file_as_it_was(text: str) -> None:
    result = add_role_lines(text, "compras")

    assert result.endswith("\n") == text.endswith("\n")
    assert ("ROLE_TOKEN_COMPRAS=" in result) == ("ROLE_TOKEN_RH" in text)
