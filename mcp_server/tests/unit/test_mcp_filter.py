from types import SimpleNamespace
from typing import Any, cast

import pytest
from conftest import ROLE_TOKENS
from fastapi import FastAPI
from mcp.server.context import HandlerResult, ServerRequestContext
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ListToolsResult, Tool

from assistant.mcp_app import RoleToolFilter, _call


def _context(method: str, role: str | None) -> ServerRequestContext[Any, Any]:
    headers = {"authorization": f"Bearer {ROLE_TOKENS[role]}"} if role else {}
    return cast(
        ServerRequestContext[Any, Any],
        SimpleNamespace(method=method, request=SimpleNamespace(headers=headers)),
    )


def _tools(*names: str) -> ListToolsResult:
    return ListToolsResult(tools=[Tool(name=n, input_schema={"type": "object"}) for n in names])


@pytest.mark.parametrize(
    ("role", "expected"), [("gerente", ["aprobar"]), ("ventas", []), (None, [])]
)
async def test_filter_works_with_model_results(
    app: FastAPI, role: str | None, expected: list[str]
) -> None:
    async def call_next(_: ServerRequestContext[Any, Any]) -> HandlerResult:
        return _tools("aprobar")

    result = await RoleToolFilter(app.state.services)(_context("tools/list", role), call_next)

    assert isinstance(result, ListToolsResult)
    assert [t.name for t in result.tools] == expected


async def test_filter_leaves_other_results_untouched(app: FastAPI) -> None:
    async def call_next(_: ServerRequestContext[Any, Any]) -> HandlerResult:
        return None

    middleware = RoleToolFilter(app.state.services)

    assert await middleware(_context("tools/list", "ventas"), call_next) is None
    assert await middleware(_context("ping", "ventas"), call_next) is None


async def test_call_without_http_headers_is_rejected(app: FastAPI) -> None:
    async def never_called(_: object) -> None:
        raise AssertionError

    stdio_like = cast(Context, SimpleNamespace(headers=None))

    with pytest.raises(ToolError, match="Token inválido o ausente"):
        await _call(app.state.services, stdio_like, never_called)
