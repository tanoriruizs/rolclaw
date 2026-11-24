from pathlib import Path

import pytest

from check_env import check, fingerprint, main, parse_env

EXAMPLE = {
    "AUTH_MODE": "demo",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_BOT_TOKEN_VENTAS": "",
    "TELEGRAM_BOT_TOKEN_GERENTE": "",
    "ROLE_TOKEN_VENTAS": "",
    "ROLE_TOKEN_GERENTE": "",
}
BASE = {
    "ANTHROPIC_API_KEY": "valor-privado-uno",
    "SERVICENOW_CLIENT_SECRET": "valor-privado-dos",
    "OPENCLAW_GATEWAY_TOKEN": "valor-privado-tres",
}
DEMO = BASE | {
    "TELEGRAM_BOT_TOKEN_VENTAS": "1:a",
    "TELEGRAM_BOT_TOKEN_GERENTE": "2:b",
    "ROLE_TOKEN_VENTAS": "v" * 40,
    "ROLE_TOKEN_GERENTE": "g" * 40,
}
SECRET = "secreto-del-directorio-" + "0" * 32
DIRECTORY = BASE | {
    "AUTH_MODE": "directorio",
    "DIRECTORY_TOKEN_SECRET": SECRET,
    "TELEGRAM_BOT_TOKEN": "9:z",
    "TELEGRAM_USERS": "E-101:111",
}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "empresa").mkdir()
    (tmp_path / "empresa/empleados.yaml").write_text("empleados: []\n", encoding="utf-8")
    (tmp_path / "empresa/roles.yaml").write_text("roles: {}\n", encoding="utf-8")
    return tmp_path


def _generate(root: Path, env: dict[str, str]) -> None:
    local = root / "openclaw/local"
    local.mkdir(parents=True)
    (local / "openclaw.json5").write_text("{}\n", encoding="utf-8")
    stamp = fingerprint(root, env["DIRECTORY_TOKEN_SECRET"], env["TELEGRAM_USERS"])
    (local / "huella").write_text(stamp + "\n", encoding="utf-8")


# --- Modo demo ---------------------------------------------------------------------------


def test_complete_demo_env_is_accepted(root: Path) -> None:
    assert check(root, DEMO, EXAMPLE) == []
    assert check(root, DEMO | {"AUTH_MODE": ""}, EXAMPLE) == []  # vacío = demo
    assert check(root, DEMO | {"AUTH_MODE": "demo"}, EXAMPLE) == []


@pytest.mark.parametrize(
    ("missing", "expected"),
    [
        ("ANTHROPIC_API_KEY", "Falta ANTHROPIC_API_KEY en .env (pega tu API key de Anthropic)."),
        ("OPENCLAW_GATEWAY_TOKEN", "Falta OPENCLAW_GATEWAY_TOKEN en .env (ejecuta make tokens)."),
        ("ROLE_TOKEN_GERENTE", "Falta ROLE_TOKEN_GERENTE en .env (ejecuta make tokens)."),
        (
            "TELEGRAM_BOT_TOKEN_VENTAS",
            "Falta TELEGRAM_BOT_TOKEN_VENTAS en .env (crea el bot con @BotFather).",
        ),
    ],
)
def test_each_missing_variable_is_named(root: Path, missing: str, expected: str) -> None:
    empty = DEMO | {missing: ""}
    absent = {key: value for key, value in DEMO.items() if key != missing}

    assert check(root, empty, EXAMPLE) == [expected]
    assert check(root, absent, EXAMPLE) == [expected]


def test_demo_does_not_ask_for_the_directory_variables(root: Path) -> None:
    # TELEGRAM_BOT_TOKEN (sin rol) es del modo directorio: en demo no hace falta.
    assert (
        check(root, DEMO | {"TELEGRAM_BOT_TOKEN": "", "DIRECTORY_TOKEN_SECRET": ""}, EXAMPLE) == []
    )


def test_roles_come_from_the_template_not_from_code(root: Path) -> None:
    example = EXAMPLE | {"ROLE_TOKEN_COMPRAS": "", "TELEGRAM_BOT_TOKEN_COMPRAS": ""}

    assert check(root, DEMO, example) == [
        "Falta ROLE_TOKEN_COMPRAS en .env (ejecuta make tokens).",
        "Falta TELEGRAM_BOT_TOKEN_COMPRAS en .env (crea el bot con @BotFather).",
    ]


def test_unknown_mode_is_rejected(root: Path) -> None:
    assert check(root, DEMO | {"AUTH_MODE": "produccion"}, EXAMPLE) == [
        "AUTH_MODE debe ser demo o directorio."
    ]


# --- Modo directorio ---------------------------------------------------------------------


def test_directory_does_not_need_role_tokens_or_role_bots(root: Path) -> None:
    _generate(root, DIRECTORY)

    assert check(root, DIRECTORY, EXAMPLE) == []


@pytest.mark.parametrize(
    ("missing", "hint"),
    [
        ("DIRECTORY_TOKEN_SECRET", "ejecuta make tokens"),
        ("TELEGRAM_BOT_TOKEN", "crea el bot con @BotFather"),
        ("TELEGRAM_USERS", "formato E-101:111111,E-001:222222"),
    ],
)
def test_directory_variables_are_required(root: Path, missing: str, hint: str) -> None:
    _generate(root, DIRECTORY)

    assert check(root, DIRECTORY | {missing: ""}, EXAMPLE) == [f"Falta {missing} en .env ({hint})."]


def test_directory_secret_must_be_long(root: Path) -> None:
    assert check(root, DIRECTORY | {"DIRECTORY_TOKEN_SECRET": "corto"}, EXAMPLE) == [
        "DIRECTORY_TOKEN_SECRET es demasiado corto: ejecuta make tokens."
    ]


def test_directory_needs_the_generated_files(root: Path) -> None:
    expected = ["Falta openclaw/local/: ejecuta make directorio."]

    assert check(root, DIRECTORY, EXAMPLE) == expected
    _generate(root, DIRECTORY)
    (root / "openclaw/local/openclaw.json5").unlink()
    assert check(root, DIRECTORY, EXAMPLE) == expected


@pytest.mark.parametrize(
    "change",
    [
        {"TELEGRAM_USERS": "E-101:111,E-102:222"},
        {"DIRECTORY_TOKEN_SECRET": "otro-secreto-del-directorio-" + "1" * 32},
    ],
)
def test_directory_generated_with_other_values_is_stale(root: Path, change: dict[str, str]) -> None:
    _generate(root, DIRECTORY)

    (problem,) = check(root, DIRECTORY | change, EXAMPLE)

    assert problem.startswith("La config del directorio está desactualizada")
    assert problem.endswith("ejecuta make directorio.")


def test_directory_is_stale_when_the_company_changes(root: Path) -> None:
    _generate(root, DIRECTORY)
    (root / "empresa/empleados.yaml").write_text("empleados: [nuevo]\n", encoding="utf-8")

    assert len(check(root, DIRECTORY, EXAMPLE)) == 1


def test_fingerprint_ignores_spaces_around_the_users(root: Path) -> None:
    assert fingerprint(root, SECRET, " E-101:111 ") == fingerprint(root, SECRET, "E-101:111")
    assert fingerprint(root, SECRET, "E-101:111") != fingerprint(root, SECRET, "E-101:112")
    assert SECRET not in fingerprint(root, SECRET, "E-101:111")


# --- Lectura de .env y salida ------------------------------------------------------------


def test_parse_env_handles_comments_quotes_and_spaces() -> None:
    text = (
        "# comentario\n"
        "A=1\n"
        " B = dos palabras \n"
        'C="entre comillas"\n'
        "D='simples'\n"
        "E=\n"
        "#F=comentada\n"
        "sin_igual\n"
        "G=a=b\n"
        'H="\n'
    )

    assert parse_env(text) == {
        "A": "1",
        "B": "dos palabras",
        "C": "entre comillas",
        "D": "simples",
        "E": "",
        "G": "a=b",
        "H": '"',
    }


def _write_env(root: Path, env: dict[str, str]) -> None:
    (root / ".env").write_text(
        "".join(f"{key}={value}\n" for key, value in env.items()), encoding="utf-8"
    )
    (root / ".env.example").write_text("".join(f"{key}=\n" for key in EXAMPLE), encoding="utf-8")


def test_main_is_silent_when_everything_is_ready(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_env(root, DEMO)

    assert main(["--root", str(root)]) == 0
    assert capsys.readouterr() == ("", "")


def test_main_lists_the_problems_without_showing_values(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_env(root, DEMO | {"ROLE_TOKEN_GERENTE": "", "TELEGRAM_BOT_TOKEN_VENTAS": ""})

    assert main(["--root", str(root)]) == 1

    captured = capsys.readouterr()
    assert captured.err.splitlines() == [
        "Falta TELEGRAM_BOT_TOKEN_VENTAS en .env (crea el bot con @BotFather).",
        "Falta ROLE_TOKEN_GERENTE en .env (ejecuta make tokens).",
    ]
    assert not [value for value in DEMO.values() if value in captured.err + captured.out]


def test_main_without_env_file(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--root", str(root)]) == 1
    assert capsys.readouterr().err == "No existe .env: ejecuta make tokens.\n"


def test_main_works_without_the_template(root: Path) -> None:
    _write_env(root, BASE)
    (root / ".env.example").unlink()

    assert main(["--root", str(root)]) == 0  # sin plantilla no se sabe qué roles hay
