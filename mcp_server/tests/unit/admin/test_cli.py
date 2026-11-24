import stat
from pathlib import Path

import pytest
from conftest import DIRECTORY_SECRET
from pydantic import SecretStr

import check_env
from assistant.admin.__main__ import main
from assistant.admin.openclaw_config import fingerprint
from assistant.auth import employee_token
from assistant.company import load_company

Capture = pytest.CaptureFixture[str]


def _run(root: Path, *args: str) -> int:
    return main(["--root", str(root), *args])


def test_validar_reports_success(repo_copy: Path, capsys: Capture) -> None:
    assert _run(repo_copy, "validar") == 0

    out = capsys.readouterr().out
    assert "4 roles · 6 empleados" in out
    assert out.endswith("Todo en orden.\n")


def test_validar_fails_with_the_list_of_problems(repo_copy: Path, capsys: Capture) -> None:
    (repo_copy / "openclaw/agentes/rh/SOUL.md").unlink()
    (repo_copy / "openclaw/agentes/rh/USER.md").unlink()

    assert _run(repo_copy, "validar") == 1

    captured = capsys.readouterr()
    assert captured.err.count("Error: ") == 2
    assert "Todo en orden" not in captured.out


def test_validar_shows_warnings(repo_copy: Path, capsys: Capture) -> None:
    for document in (repo_copy / "empresa/documentos").glob("*.md"):
        text = document.read_text(encoding="utf-8")
        document.write_text(
            text.replace(", rh", "").replace("rh, ", "").replace("[rh]", "[gerente]"),
            encoding="utf-8",
        )

    assert _run(repo_copy, "validar") == 0
    assert "Aviso: el rol 'rh' no puede leer ningún documento" in capsys.readouterr().out


def test_invalid_company_is_an_error_not_a_traceback(repo_copy: Path, capsys: Capture) -> None:
    (repo_copy / "empresa/roles.yaml").write_text("roles: {", encoding="utf-8")

    assert _run(repo_copy, "validar") == 1
    assert _run(repo_copy, "openclaw") == 1
    assert "roles.yaml: YAML inválido" in capsys.readouterr().err


def test_openclaw_check_then_generate(repo_copy: Path, capsys: Capture) -> None:
    config = repo_copy / "openclaw/openclaw.json5"
    assert _run(repo_copy, "openclaw", "--check") == 0
    original = config.read_text(encoding="utf-8")
    config.write_text("// editado a mano\n", encoding="utf-8")

    assert _run(repo_copy, "openclaw", "--check") == 1
    assert config.read_text(encoding="utf-8") == "// editado a mano\n"  # --check no escribe
    assert "Desactualizado: openclaw/openclaw.json5" in capsys.readouterr().err

    assert _run(repo_copy, "openclaw") == 0
    assert config.read_text(encoding="utf-8") == original
    assert "Generado: openclaw/openclaw.json5" in capsys.readouterr().out

    assert _run(repo_copy, "openclaw") == 0
    assert capsys.readouterr().out == "Sin cambios.\n"


def test_nuevo_rol_creates_it_and_explains_what_is_next(repo_copy: Path, capsys: Capture) -> None:
    code = _run(
        repo_copy,
        "nuevo-rol",
        "compras",
        "--nombre",
        "Compras",
        "--persona",
        "Carla Ríos",
        "--herramientas",
        "buscar_conocimiento, calcular",
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "Rol 'compras' creado." in out
    assert "  openclaw/agentes/compras/SOUL.md\n" in out
    assert "TELEGRAM_BOT_TOKEN_COMPRAS en .env" in out
    assert load_company(repo_copy / "empresa").roles["compras"].herramientas == {
        "buscar_conocimiento",
        "calcular",
    }
    assert _run(repo_copy, "validar") == 0


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["ventas", "--nombre", "Ventas", "--persona", "Ana"], "el rol 'ventas' ya existe"),
        (["Mal Id", "--nombre", "X", "--persona", "Ana"], "minúsculas"),
        (
            ["compras", "--nombre", "X", "--persona", "Ana", "--herramientas", "volar"],
            "herramienta desconocida. Opciones: buscar_conocimiento",
        ),
    ],
)
def test_nuevo_rol_errors_are_explained(
    repo_copy: Path, capsys: Capture, args: list[str], expected: str
) -> None:
    assert _run(repo_copy, "nuevo-rol", *args) == 1
    assert expected in capsys.readouterr().err


def test_a_command_is_required(repo_copy: Path) -> None:
    with pytest.raises(SystemExit) as error:
        _run(repo_copy)

    assert error.value.code == 2


# --- directorio --------------------------------------------------------------------------


@pytest.fixture
def directory_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIRECTORY_TOKEN_SECRET", DIRECTORY_SECRET)
    monkeypatch.setenv("TELEGRAM_USERS", "E-001:9001,E-101:9101,E-102:9102")


@pytest.mark.usefixtures("directory_env")
def test_directorio_writes_the_local_files(repo_copy: Path, capsys: Capture) -> None:
    assert _run(repo_copy, "directorio") == 0

    local = repo_copy / "openclaw/local"
    env_file = local / "directorio.env"
    out = capsys.readouterr().out
    assert sorted(p.name for p in (local / "agentes").iterdir()) == ["e-001", "e-101", "e-102"]
    assert (local / "openclaw.json5").is_file()
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert "Modo directorio listo en openclaw/local/ (3 personas):" in out
    assert "  E-102  Sofía Herrera  ·  ventas\n" in out
    assert "Sin Telegram registrado (no podrán usar el bot): Ana Torres, Juan Méndez" in out
    # Ni el secreto ni los tokens salen por pantalla.
    assert DIRECTORY_SECRET not in out
    assert employee_token(SecretStr(DIRECTORY_SECRET), "E-101") not in out
    assert employee_token(SecretStr(DIRECTORY_SECRET), "E-101") in env_file.read_text()


@pytest.mark.usefixtures("directory_env")
def test_directorio_does_not_touch_the_demo_files(repo_copy: Path) -> None:
    demo = repo_copy / "openclaw/openclaw.json5"
    before = demo.read_text(encoding="utf-8")

    assert _run(repo_copy, "directorio") == 0

    assert demo.read_text(encoding="utf-8") == before
    assert _run(repo_copy, "validar") == 0  # openclaw/local no cuenta como carpeta de un rol


@pytest.mark.usefixtures("directory_env")
def test_directorio_forgets_people_who_left(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(repo_copy, "directorio")
    monkeypatch.setenv("TELEGRAM_USERS", "E-001:9001")

    assert _run(repo_copy, "directorio") == 0

    local = repo_copy / "openclaw/local"
    assert sorted(p.name for p in (local / "agentes").iterdir()) == ["e-001"]
    assert "E_101" not in (local / "directorio.env").read_text(encoding="utf-8")


@pytest.mark.usefixtures("directory_env")
def test_directorio_with_everyone_registered_prints_no_missing_list(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capture
) -> None:
    everyone = "E-001:1,E-101:2,E-102:3,E-201:4,E-301:5,E-302:6"
    monkeypatch.setenv("TELEGRAM_USERS", everyone)

    assert _run(repo_copy, "directorio") == 0
    assert "Sin Telegram registrado" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("variable", "value", "expected"),
    [
        ("DIRECTORY_TOKEN_SECRET", "", "Falta DIRECTORY_TOKEN_SECRET"),
        ("DIRECTORY_TOKEN_SECRET", "corto", "Falta DIRECTORY_TOKEN_SECRET"),
        ("TELEGRAM_USERS", "", "TELEGRAM_USERS está vacío"),
        ("TELEGRAM_USERS", "E-101", "TELEGRAM_USERS debe tener el formato"),
        ("TELEGRAM_USERS", "E-999:1", "no existen: E-999"),
    ],
)
@pytest.mark.usefixtures("directory_env")
def test_directorio_needs_a_complete_configuration(
    repo_copy: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Capture,
    variable: str,
    value: str,
    expected: str,
) -> None:
    monkeypatch.setenv(variable, value)

    assert _run(repo_copy, "directorio") == 1
    assert expected in capsys.readouterr().err
    assert not (repo_copy / "openclaw/local").exists()


# --- La huella une `make directorio` con la revisión de `make up` ------------------------

USERS = "E-001:9001,E-101:9101,E-102:9102"
READY = {
    "ANTHROPIC_API_KEY": "x",
    "SERVICENOW_CLIENT_SECRET": "x",
    "OPENCLAW_GATEWAY_TOKEN": "x",
    "AUTH_MODE": "directorio",
    "DIRECTORY_TOKEN_SECRET": DIRECTORY_SECRET,
    "TELEGRAM_BOT_TOKEN": "1:x",
    "TELEGRAM_USERS": USERS,
}


def test_both_fingerprints_are_the_same_function(repo_copy: Path) -> None:
    ours = fingerprint(repo_copy, SecretStr(DIRECTORY_SECRET), f"  {USERS} ")

    assert ours == check_env.fingerprint(repo_copy, DIRECTORY_SECRET, USERS)
    assert len(ours) == 64


@pytest.mark.usefixtures("directory_env")
def test_make_up_accepts_what_make_directorio_just_wrote(repo_copy: Path) -> None:
    assert check_env.check(repo_copy, READY, {}) == [
        "Falta openclaw/local/: ejecuta make directorio."
    ]

    assert _run(repo_copy, "directorio") == 0

    assert check_env.check(repo_copy, READY, {}) == []


@pytest.mark.parametrize(
    "change",
    [
        {"TELEGRAM_USERS": "E-001:9001"},
        {"TELEGRAM_USERS": USERS + ",E-201:9201"},
        {"DIRECTORY_TOKEN_SECRET": "otro-secreto-del-directorio-" + "1" * 32},
    ],
)
@pytest.mark.usefixtures("directory_env")
def test_make_up_notices_a_stale_directory(repo_copy: Path, change: dict[str, str]) -> None:
    _run(repo_copy, "directorio")

    problems = check_env.check(repo_copy, READY | change, {})

    assert len(problems) == 1
    assert "desactualizada" in problems[0]


@pytest.mark.usefixtures("directory_env")
def test_make_up_notices_changes_in_the_company(repo_copy: Path) -> None:
    _run(repo_copy, "directorio")
    employees = repo_copy / "empresa/empleados.yaml"
    employees.write_text(
        employees.read_text(encoding="utf-8").replace("rol: ventas", "rol: empleado", 1),
        encoding="utf-8",
    )

    assert "desactualizada" in check_env.check(repo_copy, READY, {})[0]
