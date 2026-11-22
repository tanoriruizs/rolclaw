import pytest
from pydantic import SecretStr

from assistant.auth import TokenAuthenticator
from assistant.company import Company
from assistant.config import Settings
from assistant.errors import ConfigurationError


def test_each_token_resolves_to_its_role_and_persona(
    company: Company, role_tokens: dict[str, SecretStr]
) -> None:
    auth = TokenAuthenticator(role_tokens, company)

    for role, token in role_tokens.items():
        caller = auth.authenticate(token.get_secret_value())
        assert caller is not None
        assert caller.role == role
    ventas = auth.authenticate(role_tokens["ventas"].get_secret_value())
    assert ventas is not None
    assert (ventas.name, ventas.employee_id) == ("Pedro Ramírez", "E-101")


@pytest.mark.parametrize("token", ["", "incorrecto", "ventas", "ñandú-no-ascii"])
def test_unknown_token_is_rejected(
    company: Company, role_tokens: dict[str, SecretStr], token: str
) -> None:
    assert TokenAuthenticator(role_tokens, company).authenticate(token) is None


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda t: t.pop("gerente"), "ROLE_TOKEN_GERENTE"),
        (lambda t: t.update(compras=SecretStr("c" * 40)), "no existen"),
        (lambda t: t.update(rh=SecretStr("corto")), "demasiado cortos"),
        (lambda t: t.update(rh=t["ventas"]), "compartir"),
    ],
)
def test_invalid_token_configuration_stops_startup(
    company: Company, role_tokens: dict[str, SecretStr], change: object, expected: str
) -> None:
    assert callable(change)
    change(role_tokens)

    with pytest.raises(ConfigurationError, match=expected):
        TokenAuthenticator(role_tokens, company)


def test_settings_read_role_tokens_from_environment() -> None:
    environ = {"ROLE_TOKEN_VENTAS": "a" * 40, "ROLE_TOKEN_RH": "", "OTRA": "x"}

    settings = Settings.from_environ(environ)

    assert set(settings.role_tokens) == {"ventas"}
    assert "a" * 40 not in repr(settings)
