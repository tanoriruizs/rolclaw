import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sandbox_runner.app import create_app
from sandbox_runner.executor import Executor
from sandbox_runner.health import check, main


def test_run_and_health_endpoints(tmp_path: Path) -> None:
    client = TestClient(create_app(Executor(work_root=tmp_path, kill_strays=False)))

    assert client.get("/health").json() == {"status": "ok"}
    body = client.post("/run", json={"codigo": "2 ** 10"}).json()
    assert body["resultado"] == "1024"
    assert set(body) == {"stdout", "resultado", "error", "duracion_ms", "truncado"}


def test_default_app_reads_kill_strays_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SANDBOX_KILL_STRAYS", raising=False)

    assert TestClient(create_app()).get("/health").status_code == 200


@pytest.fixture
async def fake_unix_server(tmp_path: Path) -> AsyncIterator[Path]:
    socket_path = tmp_path / "sandbox.sock"

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(handle, path=str(socket_path))
    async with server:
        yield socket_path


async def test_healthcheck_over_a_unix_socket(fake_unix_server: Path) -> None:
    assert await asyncio.to_thread(check, str(fake_unix_server)) is True


def test_healthcheck_fails_without_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_SOCKET", str(tmp_path / "no-existe.sock"))

    assert main() == 1


async def test_healthcheck_main_succeeds(
    fake_unix_server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SANDBOX_SOCKET", str(fake_unix_server))

    assert await asyncio.to_thread(main) == 0
