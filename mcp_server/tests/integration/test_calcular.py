from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest
from conftest import ROLE_TOKENS
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from assistant.sandbox_client import SandboxClient, SandboxUnavailableError

AuthHeaders = Callable[[str], dict[str, str]]
BASE_URL = "http://127.0.0.1:8000"


@pytest.mark.parametrize("role", ["ventas", "rh", "empleado", "gerente"])
def test_every_role_can_calculate(client: TestClient, auth_headers: AuthHeaders, role: str) -> None:
    response = client.post(
        "/api/v1/calcular", json={"codigo": "50 * 15000 * 0.85"}, headers=auth_headers(role)
    )

    assert response.status_code == 200
    assert response.json()["resultado"] == "637500.0"


def test_calculation_errors_are_data_not_http_errors(
    client: TestClient, auth_headers: AuthHeaders
) -> None:
    body = client.post(
        "/api/v1/calcular", json={"codigo": "1 / 0"}, headers=auth_headers("ventas")
    ).json()

    assert body["error"] == "ZeroDivisionError: division by zero"
    assert body["resultado"] is None


@pytest.mark.parametrize("codigo", ["", "   ", "x" * 10_001])
def test_empty_or_huge_code_is_rejected(
    client: TestClient, auth_headers: AuthHeaders, codigo: str
) -> None:
    response = client.post(
        "/api/v1/calcular", json={"codigo": codigo}, headers=auth_headers("ventas")
    )

    assert response.status_code == 422


@asynccontextmanager
async def _mcp(app: FastAPI, role: str) -> AsyncIterator[Client]:
    headers = {"Authorization": f"Bearer {ROLE_TOKENS[role]}"}
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with (
            httpx2.AsyncClient(transport=transport, base_url=BASE_URL, headers=headers) as http,
            Client(streamable_http_client(f"{BASE_URL}/mcp", http_client=http)) as client,
        ):
            yield client


async def test_demo_business_days_over_mcp(app: FastAPI) -> None:
    code = (
        "from datetime import date, timedelta\n"
        "d = date(2026, 12, 14)\n"
        "sum((d + timedelta(i)).weekday() < 5 for i in range(5))"
    )
    async with _mcp(app, "empleado") as client:
        result = await client.call_tool("calcular", {"codigo": code})

    data: Any = result.structured_content
    assert not result.is_error
    assert data["resultado"] == "5"


def _client(handler: Callable[[httpx2.Request], httpx2.Response]) -> SandboxClient:
    transport = httpx2.MockTransport(handler)
    return SandboxClient(httpx2.AsyncClient(transport=transport, base_url="http://sandbox"))


async def test_sandbox_down_is_a_useful_502() -> None:
    def down(_: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no existe el socket")

    with pytest.raises(SandboxUnavailableError, match="servicio de cálculo no está disponible"):
        await _client(down).run("1 + 1")


async def test_sandbox_server_error_is_a_useful_502() -> None:
    with pytest.raises(SandboxUnavailableError):
        await _client(lambda _: httpx2.Response(500)).run("1 + 1")
