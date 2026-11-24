import re
from pathlib import Path
from typing import Any

import pytest
from conftest import DIRECTORY_SECRET, REPO, parse_json5
from pydantic import SecretStr

from assistant.admin.openclaw_config import (
    AGENTS_DIR,
    DEMO_CONFIG,
    LOCAL_DIR,
    TEMPLATE,
    OpenClawConfigError,
    demo_blocks,
    demo_files,
    directory_blocks,
    directory_files,
    render,
)
from assistant.auth import TokenAuthenticator, employee_token
from assistant.company import Company

SECRET = SecretStr(DIRECTORY_SECRET)
CHATS = {"E-001": "9001", "E-101": "9101", "E-102": "9102", "E-301": "9301"}
OPENCLAW_TOOL_GROUPS = {
    "group:runtime",
    "group:fs",
    "group:web",
    "group:ui",
    "group:automation",
    "group:nodes",
    "group:messaging",
}


def _demo(company: Company) -> Any:
    return parse_json5(demo_files(REPO, company)[DEMO_CONFIG])


def _directory(company: Company, chats: dict[str, str] = CHATS) -> Any:
    files = directory_files(REPO, company, SECRET, chats)
    return parse_json5(files[LOCAL_DIR / "openclaw.json5"])


# --- Modo demo ---------------------------------------------------------------------------


def test_the_committed_files_are_up_to_date(company: Company) -> None:
    for path, content in demo_files(REPO, company).items():
        assert (REPO / path).read_text(encoding="utf-8") == content, (
            f"{path} está desactualizado: ejecuta make openclaw-config"
        )


def test_demo_has_one_agent_server_bot_and_binding_per_role(company: Company) -> None:
    config = _demo(company)
    roles = list(company.roles)

    assert list(config["mcp"]["servers"]) == [f"cardinal-{role}" for role in roles]
    assert list(config["agents"]["entries"]) == ["sistema", *roles]
    assert list(config["channels"]["telegram"]["accounts"]) == roles
    assert config["bindings"] == [
        {"agentId": role, "match": {"channel": "telegram", "accountId": role}} for role in roles
    ]
    for role in roles:
        server = config["mcp"]["servers"][f"cardinal-{role}"]
        assert server["headers"] == {"Authorization": f"Bearer ${{ROLE_TOKEN_{role.upper()}}}"}
        account = config["channels"]["telegram"]["accounts"][role]
        assert account["botToken"]["id"] == f"TELEGRAM_BOT_TOKEN_{role.upper()}"


def test_each_agent_can_only_use_its_own_mcp_server(company: Company) -> None:
    entries = _demo(company)["agents"]["entries"]

    assert entries["sistema"]["tools"] == {"allow": []}
    for role in company.roles:
        assert entries[role]["tools"] == {"allow": [f"cardinal-{role}__*"]}
        assert entries[role]["workspace"] == f"/home/node/.openclaw/workspace-{role}"
    workspaces = [entry["workspace"] for entry in entries.values()]
    assert len(set(workspaces)) == len(workspaces)


@pytest.mark.parametrize("mode", ["demo", "directorio"])
def test_the_hardening_of_the_template_survives_generation(company: Company, mode: str) -> None:
    config = _demo(company) if mode == "demo" else _directory(company)

    assert set(config["tools"]["deny"]) == OPENCLAW_TOOL_GROUPS
    assert config["tools"]["sessions"] == {"visibility": "self"}
    assert config["tools"]["agentToAgent"] == {"enabled": False}
    assert config["tools"]["elevated"] == {"enabled": False}
    assert config["cron"] == {"enabled": False}
    assert config["agents"]["ownership"] == "explicit"
    assert config["gateway"]["auth"]["mode"] == "token"
    assert config["channels"]["telegram"]["configWrites"] is False
    assert config["channels"]["telegram"]["dmPolicy"] in {"pairing", "allowlist"}


def test_generated_config_carries_no_secrets(company: Company) -> None:
    text = demo_files(REPO, company)[DEMO_CONFIG]

    assert text.startswith("// GENERADO")
    assert re.findall(r"Bearer (?!\$\{[A-Z0-9_]+\})", text) == []
    assert "__" not in re.sub(r"cardinal-\w+__\*", "", text)  # no quedó ningún marcador


def test_demo_also_writes_who_each_agent_talks_to(company: Company) -> None:
    files = demo_files(REPO, company)

    user = files[AGENTS_DIR / "ventas" / "USER.md"]
    assert "- Nombre: Pedro Ramírez\n" in user
    assert "- Puesto: Ejecutivo de ventas (Ventas)\n" in user
    assert set(files) == {DEMO_CONFIG} | {AGENTS_DIR / role / "USER.md" for role in company.roles}


def test_role_souls_do_not_name_a_person(company: Company) -> None:
    names = [employee.nombre.split()[0] for employee in company.empleados]

    for role in company.roles:
        soul = (REPO / AGENTS_DIR / role / "SOUL.md").read_text(encoding="utf-8")
        assert not [name for name in names if name in soul], role


# --- Plantilla ---------------------------------------------------------------------------


def test_render_keeps_indentation_and_comments() -> None:
    template = "{\n  // comentario\n  a: {\n    b: __SERVIDORES_MCP__,\n  },\n}\n"

    text = render(template, {"__SERVIDORES_MCP__": {"x": [1, 2]}})

    assert "  // comentario\n" in text
    assert '    b: {\n      "x": [\n        1,\n        2\n      ]\n    },\n' in text
    assert parse_json5(text) == {"a": {"b": {"x": [1, 2]}}}


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("{ a: __OTRO__ }", "marcador desconocido: __OTRO__"),
        ("{ a: 1 }", "una sola vez cada marcador: __AGENTES__"),
        ("{\n a: __AGENTES__,\n b: __AGENTES__\n}", "una sola vez cada marcador: __AGENTES__"),
    ],
)
def test_broken_templates_are_reported(template: str, expected: str) -> None:
    with pytest.raises(OpenClawConfigError, match=expected):
        render(template, {"__AGENTES__": {}})


def test_missing_template_is_reported(tmp_path: Path, company: Company) -> None:
    with pytest.raises(OpenClawConfigError, match=r"falta el archivo .*plantilla"):
        demo_files(tmp_path, company)


def test_roles_cannot_take_reserved_agent_names(company: Company) -> None:
    roles = dict(company.roles) | {"sistema": company.roles["ventas"]}
    clash = company.model_construct(**{**dict(company), "roles": roles})

    with pytest.raises(OpenClawConfigError, match="reservados: sistema"):
        demo_blocks(clash)


# --- Modo directorio: un bot, un agente por persona --------------------------------------


def test_directory_has_one_agent_per_registered_person(company: Company) -> None:
    config = _directory(company)

    assert list(config["agents"]["entries"]) == [
        "sistema",
        "invitado",
        "e-001",
        "e-101",
        "e-102",
        "e-301",
    ]
    assert config["agents"]["entries"]["e-102"]["name"] == "Sofía Herrera"
    assert list(config["mcp"]["servers"]) == [
        "cardinal-e-001",
        "cardinal-e-101",
        "cardinal-e-102",
        "cardinal-e-301",
    ]
    for agent_id, entry in config["agents"]["entries"].items():
        own = [] if agent_id in {"sistema", "invitado"} else [f"cardinal-{agent_id}__*"]
        assert entry["tools"] == {"allow": own}


def test_directory_strangers_reach_an_agent_without_tools(company: Company) -> None:
    config = _directory(company)
    telegram = config["channels"]["telegram"]
    defaults = [k for k, entry in config["agents"]["entries"].items() if entry.get("default")]

    # Solo escriben quienes están en la lista; y si algo no coincide, cae en "invitado".
    assert defaults == ["invitado"]
    assert telegram["dmPolicy"] == "allowlist"
    assert telegram["allowFrom"] == ["9001", "9101", "9102", "9301"]
    assert telegram["groupPolicy"] == "disabled"
    assert telegram["botToken"] == {
        "source": "env",
        "provider": "default",
        "id": "TELEGRAM_BOT_TOKEN",
    }
    assert "accounts" not in telegram


def test_directory_each_telegram_chat_ends_up_as_the_right_employee(company: Company) -> None:
    """La cadena completa: chat -> agente -> servidor MCP -> token -> persona en el servidor."""
    files = directory_files(REPO, company, SECRET, CHATS)
    config = parse_json5(files[LOCAL_DIR / "openclaw.json5"])
    env = dict(line.split("=", 1) for line in files[LOCAL_DIR / "directorio.env"].splitlines())
    auth = TokenAuthenticator.for_directory(SECRET, company)
    by_chat = {b["match"]["peer"]["id"]: b for b in config["bindings"]}

    assert set(by_chat) == set(CHATS.values())
    for employee_id, chat in CHATS.items():
        binding = by_chat[chat]
        assert binding["match"] == {"channel": "telegram", "peer": {"kind": "direct", "id": chat}}
        (allowed,) = config["agents"]["entries"][binding["agentId"]]["tools"]["allow"]
        server = config["mcp"]["servers"][allowed.removesuffix("__*")]
        variable = re.fullmatch(r"Bearer \$\{(\w+)\}", server["headers"]["Authorization"])
        assert variable is not None
        caller = auth.authenticate(env[variable.group(1)])
        assert caller is not None
        assert caller.employee_id == employee_id


def test_directory_tokens_live_only_in_the_env_file(company: Company) -> None:
    files = directory_files(REPO, company, SECRET, CHATS)
    env_path = LOCAL_DIR / "directorio.env"
    tokens = [employee_token(SECRET, employee_id) for employee_id in CHATS]

    assert files[env_path].splitlines() == [
        f"EMPLOYEE_TOKEN_{employee_id.replace('-', '_')}={token}"
        for employee_id, token in zip(CHATS, tokens, strict=True)
    ]
    for path, content in files.items():
        if path != env_path:
            assert not [t for t in tokens if t in content], path
        assert DIRECTORY_SECRET not in content
    assert "openclaw/local/" in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_directory_agents_get_the_soul_of_their_role_and_their_own_name(
    company: Company,
) -> None:
    files = directory_files(REPO, company, SECRET, CHATS)
    ventas_soul = (REPO / AGENTS_DIR / "ventas" / "SOUL.md").read_text(encoding="utf-8")

    assert files[LOCAL_DIR / "agentes" / "e-101" / "SOUL.md"] == ventas_soul
    assert files[LOCAL_DIR / "agentes" / "e-102" / "SOUL.md"] == ventas_soul
    assert "- Nombre: Sofía Herrera\n" in files[LOCAL_DIR / "agentes" / "e-102" / "USER.md"]
    assert "no tiene acceso" in files[LOCAL_DIR / "agentes" / "invitado" / "SOUL.md"]
    assert LOCAL_DIR / "agentes" / "e-302" / "SOUL.md" not in files  # Mariana no dio su chat


@pytest.mark.parametrize(
    ("chats", "expected"),
    [
        ({}, "TELEGRAM_USERS está vacío"),
        ({"E-999": "1"}, "no existen: E-999"),
        ({"E-101": "5", "E-102": "5"}, "chat de Telegram repetido entre empleados: 5"),
        ({"E-101": "-100123"}, "chats personales, no grupos: E-101"),
    ],
)
def test_directory_rejects_ambiguous_or_unknown_people(
    company: Company, chats: dict[str, str], expected: str
) -> None:
    with pytest.raises(OpenClawConfigError, match=expected):
        directory_blocks(company, chats)


def test_directory_rejects_employee_ids_that_collide(company: Company) -> None:
    twin = company.empleados[1].model_copy(update={"id": "E_101"})
    clash = company.model_construct(**{**dict(company), "empleados": (*company.empleados, twin)})

    with pytest.raises(OpenClawConfigError, match="id de agente repetido"):
        directory_blocks(clash, {"E-101": "1", "E_101": "2"})


def test_directory_rejects_reserved_employee_ids(company: Company) -> None:
    odd = company.empleados[1].model_copy(update={"id": "Invitado"})
    clash = company.model_construct(**{**dict(company), "empleados": (*company.empleados, odd)})

    with pytest.raises(OpenClawConfigError, match="reservados: invitado"):
        directory_blocks(clash, {"Invitado": "1"})


def test_template_is_the_single_source_of_the_static_part() -> None:
    template = (REPO / TEMPLATE).read_text(encoding="utf-8")

    assert template.count("__SERVIDORES_MCP__") == 1
    assert "ROLE_TOKEN" not in template
    assert "TELEGRAM_BOT_TOKEN" not in template
