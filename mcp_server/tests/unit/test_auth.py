import pytest
from conftest import DIRECTORY_SECRET
from pydantic import SecretStr

from assistant.auth import TokenAuthenticator, employee_token
from assistant.company import Company
from assistant.config import AuthMode, Settings
from assistant.errors import ConfigurationError

SECRET = SecretStr(DIRECTORY_SECRET)

# --- Modo demo: un token por rol ---------------------------------------------------------


def test_each_token_resolves_to_its_role_and_persona(
    company: Company, role_tokens: dict[str, SecretStr]
) -> None:
    auth = TokenAuthenticator.for_roles(role_tokens, company)

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
    assert TokenAuthenticator.for_roles(role_tokens, company).authenticate(token) is None


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
        TokenAuthenticator.for_roles(role_tokens, company)


def test_settings_read_role_tokens_from_environment() -> None:
    environ = {"ROLE_TOKEN_VENTAS": "a" * 40, "ROLE_TOKEN_RH": "", "OTRA": "x"}

    settings = Settings.from_environ(environ)

    assert set(settings.role_tokens) == {"ventas"}
    assert "a" * 40 not in repr(settings)


# --- Modo directorio: un token por empleado ----------------------------------------------


def test_each_employee_gets_their_own_identity_and_role(company: Company) -> None:
    auth = TokenAuthenticator.for_directory(SECRET, company)

    for employee in company.empleados:
        caller = auth.authenticate(employee_token(SECRET, employee.id))
        assert caller is not None
        assert (caller.employee_id, caller.name, caller.role) == (
            employee.id,
            employee.nombre,
            employee.rol,
        )


def test_two_people_with_the_same_role_are_different_callers(company: Company) -> None:
    auth = TokenAuthenticator.for_directory(SECRET, company)

    pedro = auth.authenticate(employee_token(SECRET, "E-101"))
    sofia = auth.authenticate(employee_token(SECRET, "E-102"))

    assert pedro is not None
    assert sofia is not None
    assert pedro.role == sofia.role == "ventas"
    assert pedro.employee_id != sofia.employee_id


def test_employee_tokens_are_unique_stable_and_long(company: Company) -> None:
    tokens = [employee_token(SECRET, e.id) for e in company.empleados]

    assert len(set(tokens)) == len(company.empleados)
    assert tokens == [employee_token(SECRET, e.id) for e in company.empleados]
    assert all(len(token) == 43 for token in tokens)  # 256 bits en base64 sin relleno
    assert DIRECTORY_SECRET not in "".join(tokens)


def test_tokens_depend_on_the_secret(company: Company) -> None:
    other = SecretStr("otro-secreto-del-directorio-" + "1" * 32)
    auth = TokenAuthenticator.for_directory(SECRET, company)

    assert employee_token(SECRET, "E-101") != employee_token(other, "E-101")
    assert auth.authenticate(employee_token(other, "E-101")) is None


@pytest.mark.parametrize("employee_id", ["E-999", "e-101", "", "E-101 "])
def test_tokens_of_people_outside_the_directory_do_not_work(
    company: Company, employee_id: str
) -> None:
    auth = TokenAuthenticator.for_directory(SECRET, company)

    assert auth.authenticate(employee_token(SECRET, employee_id)) is None


def test_role_tokens_do_not_work_in_directory_mode(
    company: Company, role_tokens: dict[str, SecretStr]
) -> None:
    auth = TokenAuthenticator.for_directory(SECRET, company)

    for token in role_tokens.values():
        assert auth.authenticate(token.get_secret_value()) is None


@pytest.mark.parametrize("secret", [None, SecretStr(""), SecretStr("corto"), SecretStr("x" * 31)])
def test_directory_mode_needs_a_strong_secret(company: Company, secret: SecretStr | None) -> None:
    with pytest.raises(ConfigurationError, match="DIRECTORY_TOKEN_SECRET"):
        TokenAuthenticator.for_directory(secret, company)


def test_settings_choose_the_mode(company: Company, role_tokens: dict[str, SecretStr]) -> None:
    demo = Settings(role_tokens=role_tokens)
    directory = Settings(
        auth_mode=AuthMode.DIRECTORIO, role_tokens=role_tokens, directory_token_secret=SECRET
    )
    role_token = role_tokens["ventas"].get_secret_value()
    person_token = employee_token(SECRET, "E-102")

    demo_auth = TokenAuthenticator.from_settings(demo, company)
    directory_auth = TokenAuthenticator.from_settings(directory, company)

    assert demo.auth_mode is AuthMode.DEMO
    assert demo_auth.authenticate(role_token) is not None
    assert demo_auth.authenticate(person_token) is None
    assert directory_auth.authenticate(role_token) is None
    assert directory_auth.authenticate(person_token) is not None


def test_secret_never_appears_in_settings_repr() -> None:
    settings = Settings(auth_mode=AuthMode.DIRECTORIO, directory_token_secret=SECRET)

    assert DIRECTORY_SECRET not in repr(settings)
