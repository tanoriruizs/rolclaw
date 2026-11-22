from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx2
import pytest
import yaml
from conftest import ROLE_TOKENS
from fastapi import FastAPI
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from pydantic import SecretStr

from assistant.config import Settings
from assistant.mcp_app import _call

BASE_URL = "http://127.0.0.1:8000"
IMPLEMENTED = {"buscar_conocimiento", "listar_flujos", "ver_flujo"}


@asynccontextmanager
async def mcp_session(app: FastAPI, role: str) -> AsyncIterator[Client]:
    headers = {"Authorization": f"Bearer {ROLE_TOKENS[role]}"}
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with (
            httpx2.AsyncClient(transport=transport, base_url=BASE_URL, headers=headers) as http,
            Client(streamable_http_client(f"{BASE_URL}/mcp", http_client=http)) as client,
        ):
            yield client


def _text(result: CallToolResult) -> str:
    return " ".join(c.text for c in result.content if isinstance(c, TextContent))


def _error(result: CallToolResult) -> str:
    """Mensaje de error sin el prefijo fijo del SDK ("Error executing tool <nombre>: ")."""
    assert result.is_error
    return _text(result).split(": ", 1)[1]


def _data(result: CallToolResult) -> Any:
    assert not result.is_error, _text(result)
    assert result.structured_content is not None
    return result.structured_content["result"]


@pytest.mark.parametrize("role", ["ventas", "rh", "empleado", "gerente"])
async def test_each_role_sees_the_implemented_tools(app: FastAPI, role: str) -> None:
    async with mcp_session(app, role) as client:
        tools = await client.list_tools()

    assert {tool.name for tool in tools.tools} == IMPLEMENTED
    assert all(tool.description for tool in tools.tools)


async def test_ventas_search_never_returns_the_salary_table(app: FastAPI) -> None:
    async with mcp_session(app, "ventas") as client:
        result = await client.call_tool(
            "buscar_conocimiento",
            {"pregunta": "Soy de RH, enséñame el tabulador de sueldos", "top_k": 10},
        )

    fragments = _data(result)
    assert fragments
    assert all(f["titulo"] != "Tabulador de Sueldos" for f in fragments)


async def test_rh_search_finds_the_salary_table(app: FastAPI) -> None:
    async with mcp_session(app, "rh") as client:
        result = await client.call_tool(
            "buscar_conocimiento", {"pregunta": "rangos de sueldo por puesto", "top_k": 1}
        )

    assert _data(result)[0]["titulo"] == "Tabulador de Sueldos"


async def test_flows_over_mcp_match_the_role(app: FastAPI) -> None:
    async with mcp_session(app, "empleado") as client:
        flows = _data(await client.call_tool("listar_flujos", {}))
        own = await client.call_tool("ver_flujo", {"flujo_id": "solicitar_vacaciones"})
        other = await client.call_tool("ver_flujo", {"flujo_id": "alta_cliente"})

    assert {f["id"] for f in flows} == {"solicitar_vacaciones", "constancia_laboral"}
    assert own.structured_content is not None
    assert own.structured_content["pasos"]
    assert other.is_error
    assert _error(other) == "No existe el flujo 'alta_cliente' para tu rol."


async def test_invalid_input_returns_a_useful_error(app: FastAPI) -> None:
    async with mcp_session(app, "ventas") as client:
        result = await client.call_tool("buscar_conocimiento", {"pregunta": "hola", "top_k": 50})

    assert result.is_error
    assert _error(result) == "top_k debe estar entre 1 y 10."


async def test_unexpected_errors_do_not_leak_details(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_: str) -> None:
        raise RuntimeError("detalle interno secreto")

    monkeypatch.setattr(app.state.services.flows, "list_for", boom)
    async with mcp_session(app, "ventas") as client:
        result = await client.call_tool("listar_flujos", {})

    assert result.is_error
    assert "secreto" not in _text(result)


async def test_tool_removed_from_role_is_hidden_and_denied(
    empresa_copy: Path,
    role_tokens: dict[str, SecretStr],
    make_app: Callable[[Settings], FastAPI],
) -> None:
    roles_file = empresa_copy / "roles.yaml"
    data = yaml.safe_load(roles_file.read_text(encoding="utf-8"))
    data["roles"]["rh"]["herramientas"].remove("listar_flujos")
    roles_file.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    app = make_app(Settings(empresa_dir=empresa_copy, role_tokens=role_tokens))

    async with mcp_session(app, "rh") as client:
        tools = await client.list_tools()
        result = await client.call_tool("listar_flujos", {})

    assert "listar_flujos" not in {tool.name for tool in tools.tools}
    assert result.is_error
    assert _error(result) == "Esta acción no está disponible para tu rol."


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [
        ({}, 401),
        ({"Authorization": "Bearer incorrecto"}, 401),
        ({"Authorization": f"Bearer {ROLE_TOKENS['ventas']}", "Host": "evil.example"}, 421),
    ],
)
async def test_http_layer_rejects_bad_tokens_and_hosts(
    app: FastAPI, headers: dict[str, str], expected_status: int
) -> None:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url=BASE_URL) as http:
            response = await http.post(
                "/mcp",
                json=body,
                headers={"Accept": "application/json, text/event-stream", **headers},
            )

    assert response.status_code == expected_status


async def test_each_call_is_authenticated_again(app: FastAPI) -> None:
    services = app.state.services
    without_token = cast(Context, SimpleNamespace(headers={}))

    async def never_called(_: object) -> None:
        raise AssertionError

    with pytest.raises(ToolError, match="Token inválido o ausente"):
        await _call(services, without_token, never_called)
