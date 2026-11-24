import pytest

from assistant.config import AuthMode, Settings
from assistant.errors import ConfigurationError


def test_defaults_are_the_demo_mode_without_notifications() -> None:
    settings = Settings()

    assert settings.auth_mode is AuthMode.DEMO
    assert settings.telegram_chat_ids() == {}
    assert settings.telegram_bot_tokens == {}


def test_bot_tokens_are_read_per_role_and_kept_secret() -> None:
    environ = {
        "TELEGRAM_BOT_TOKEN_VENTAS": "111:aaa",
        "TELEGRAM_BOT_TOKEN_GERENTE": "222:bbb",
        "TELEGRAM_BOT_TOKEN_RH": "",
        "TELEGRAM_BOT_TOKEN": "333:ccc",  # el del modo directorio no es de ningún rol
    }

    settings = Settings.from_environ(environ)

    assert set(settings.telegram_bot_tokens) == {"ventas", "gerente"}
    assert "111:aaa" not in repr(settings)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("E-101:111111", {"E-101": "111111"}),
        (" e-101:111111 , E-001:222222 ,", {"E-101": "111111", "E-001": "222222"}),
        ("E-001:-100123", {"E-001": "-100123"}),  # los grupos de Telegram tienen id negativo
        ("", {}),
    ],
)
def test_telegram_users_are_parsed(raw: str, expected: dict[str, str]) -> None:
    assert Settings(telegram_users=raw).telegram_chat_ids() == expected


@pytest.mark.parametrize("raw", ["E-101", "E-101=111", "E-101:abc", "E-101:1:2", ":111"])
def test_malformed_telegram_users_are_reported(raw: str) -> None:
    with pytest.raises(ConfigurationError, match="TELEGRAM_USERS debe tener el formato"):
        Settings(telegram_users=raw).telegram_chat_ids()
