import json
import logging
import sys

from assistant.logs import JsonFormatter, request_id_var


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
