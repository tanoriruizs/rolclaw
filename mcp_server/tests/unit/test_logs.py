import json
import logging
import sys

from assistant.logs import JsonFormatter, configure_logging, request_id_var


def test_json_formatter_includes_request_id_and_fields() -> None:
    record = logging.LogRecord("prueba", logging.INFO, __file__, 1, "hola %s", ("mundo",), None)
    record.__dict__["fields"] = {"rol": "ventas"}
    token = request_id_var.set("abc123")
    try:
        line = JsonFormatter().format(record)
    finally:
        request_id_var.reset(token)

    payload = json.loads(line)
    assert payload["msg"] == "hola mundo"
    assert payload["request_id"] == "abc123"
    assert payload["rol"] == "ventas"


def test_json_formatter_includes_exception() -> None:
    try:
        raise ValueError("falla")
    except ValueError:
        record = logging.LogRecord("prueba", logging.ERROR, __file__, 1, "x", (), sys.exc_info())

    assert "ValueError: falla" in json.loads(JsonFormatter().format(record))["exc"]


def test_json_formatter_hides_telegram_bot_tokens_everywhere() -> None:
    url = "https://api.telegram.org/bot123456:AAH-secreto_del_bot/sendMessage"
    try:
        raise ValueError(f"fallo al llamar {url}")
    except ValueError:
        record = logging.LogRecord(
            "prueba", logging.ERROR, __file__, 1, "POST %s", (url,), sys.exc_info()
        )
    record.__dict__["fields"] = {"url": url}

    line = JsonFormatter().format(record)

    assert "AAH-secreto_del_bot" not in line
    assert line.count("bot<oculto>/sendMessage") == 3  # mensaje, campo y traceback
    assert json.loads(line)["msg"] == "POST https://api.telegram.org/bot<oculto>/sendMessage"


def test_http_client_loggers_are_silenced() -> None:
    for name in ("httpx2", "httpcore2", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.NOTSET)

    configure_logging("DEBUG")

    for name in ("httpx2", "httpcore2", "httpx", "httpcore"):
        assert logging.getLogger(name).level == logging.WARNING
