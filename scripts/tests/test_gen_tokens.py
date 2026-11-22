from pathlib import Path

import pytest

from gen_tokens import fill_secrets, main

EXAMPLE = """\
# comentario: ROLE_TOKEN_COMENTADO=
ANTHROPIC_API_KEY=
ROLE_TOKEN_VENTAS=
ROLE_TOKEN_RH=ya-tenia-valor
SERVICENOW_CLIENT_SECRET=
"""


def _values(text: str) -> dict[str, str]:
    assignments = (line for line in text.splitlines() if "=" in line and not line.startswith("#"))
    return dict(line.split("=", 1) for line in assignments)


def test_fills_only_empty_generated_keys() -> None:
    content, filled = fill_secrets(EXAMPLE)
    values = _values(content)

    assert filled == ["ROLE_TOKEN_VENTAS", "SERVICENOW_CLIENT_SECRET"]
    assert len(values["ROLE_TOKEN_VENTAS"]) >= 40
    assert values["ROLE_TOKEN_RH"] == "ya-tenia-valor"
    assert values["ANTHROPIC_API_KEY"] == ""
    assert "# comentario: ROLE_TOKEN_COMENTADO=" in content


def test_force_regenerates_existing_values() -> None:
    content, filled = fill_secrets(EXAMPLE, force=True)

    assert "ROLE_TOKEN_RH" in filled
    assert _values(content)["ROLE_TOKEN_RH"] != "ya-tenia-valor"


def test_generated_tokens_are_unique() -> None:
    content, _ = fill_secrets(EXAMPLE)
    values = _values(content)

    assert values["ROLE_TOKEN_VENTAS"] != values["SERVICENOW_CLIENT_SECRET"]


def test_main_creates_env_from_example_without_printing_secrets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    example, env = tmp_path / ".env.example", tmp_path / ".env"
    example.write_text(EXAMPLE, encoding="utf-8")

    assert main(["--env", str(env), "--example", str(example)]) == 0

    token = _values(env.read_text(encoding="utf-8"))["ROLE_TOKEN_VENTAS"]
    assert token
    assert token not in capsys.readouterr().out
    assert env.stat().st_mode & 0o777 == 0o600


def test_main_is_idempotent(tmp_path: Path) -> None:
    example, env = tmp_path / ".env.example", tmp_path / ".env"
    example.write_text(EXAMPLE, encoding="utf-8")
    args = ["--env", str(env), "--example", str(example)]

    main(args)
    first = env.read_text(encoding="utf-8")
    main(args)

    assert env.read_text(encoding="utf-8") == first


def test_main_fails_without_env_or_example(tmp_path: Path) -> None:
    args = ["--env", str(tmp_path / ".env"), "--example", str(tmp_path / "missing")]

    assert main(args) == 1
