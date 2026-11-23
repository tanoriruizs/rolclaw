from collections.abc import AsyncIterator, Callable

import httpx2
import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from pydantic import SecretStr

from assistant.servicenow_client import ServiceNowClient, ServiceNowError
from servicenow_mock.main import MockSettings, create_app

SECRET = "secreto-de-prueba-del-mock"
BASE = "http://servicenow.test"
Handler = Callable[[httpx2.Request], httpx2.Response]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _client(transport: httpx2.AsyncBaseTransport, **kwargs: object) -> ServiceNowClient:
    http = httpx2.AsyncClient(transport=transport)
    return ServiceNowClient(http, BASE, "cardinal-assistant", SecretStr(SECRET), **kwargs)  # type: ignore[arg-type]


# --- Contra el ServiceNow simulado real (en memoria) ----------------------------------


class CountingASGITransport(httpx2.ASGITransport):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.token_requests = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/oauth_token.do":
            self.token_requests += 1
        return await super().handle_async_request(request)


@pytest.fixture
async def transport() -> AsyncIterator[CountingASGITransport]:
    settings = MockSettings(servicenow_client_secret=SecretStr(SECRET), token_ttl_seconds=600)
    app = create_app(settings, redis=FakeAsyncRedis(server=FakeServer()))
    async with app.router.lifespan_context(app):
        yield CountingASGITransport(app=app)


async def test_create_get_query_and_update_against_the_mock(
    transport: CountingASGITransport,
) -> None:
    client = _client(transport)

    created = await client.create("sc_request", {"u_rol": "ventas", "approval": "requested"})
    found = await client.get("sc_request", created["sys_id"])
    pending = await client.query("sc_request", "approval=requested")
    approved = await client.update("sc_request", created["sys_id"], {"approval": "approved"})

    assert created["number"] == "REQ0010001"
    assert found == created
    assert [r["number"] for r in pending] == ["REQ0010001"]
    assert approved["approval"] == "approved"
    assert await client.get("sc_request", "no-existe") is None


async def test_token_is_cached_and_renewed_before_it_expires(
    transport: CountingASGITransport,
) -> None:
    clock = FakeClock()
    client = _client(transport, clock=clock)

    await client.query("sc_request", "")
    await client.query("sc_request", "")
    assert transport.token_requests == 1

    clock.now += 600 - 60  # el margen de un minuto obliga a renovar antes
    await client.query("sc_request", "")
    assert transport.token_requests == 2


async def test_wrong_credentials_fail_with_a_useful_message(
    transport: CountingASGITransport,
) -> None:
    http = httpx2.AsyncClient(transport=transport)
    client = ServiceNowClient(http, BASE, "cardinal-assistant", SecretStr("incorrecto"))

    with pytest.raises(ServiceNowError, match="no está disponible"):
        await client.query("sc_request", "")


# --- Fallos simulados: reintentos y backoff --------------------------------------------


def _scripted(
    *table_responses: httpx2.Response | Exception,
) -> tuple[httpx2.MockTransport, list[str]]:
    """Responde OAuth siempre bien y, en la Table API, lo que diga el guion en orden."""
    script = list(table_responses)
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/oauth_token.do":
            seen.append("token")
            return httpx2.Response(200, json={"access_token": f"t{len(seen)}", "expires_in": 1800})
        seen.append(request.headers["Authorization"])
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    return httpx2.MockTransport(handler), seen


OK = httpx2.Response(200, json={"result": [{"number": "REQ0010001"}]})


async def test_retries_5xx_with_exponential_backoff() -> None:
    transport, _ = _scripted(httpx2.Response(503), httpx2.Response(502), OK)
    sleeps = Sleeps()

    result = await _client(transport, sleep=sleeps).query("sc_request", "")

    assert result == [{"number": "REQ0010001"}]
    assert sleeps.calls == [0.5, 1.0]


async def test_respects_retry_after_on_429_with_a_cap() -> None:
    transport, _ = _scripted(
        httpx2.Response(429, headers={"Retry-After": "2"}),
        httpx2.Response(429, headers={"Retry-After": "120"}),
        OK,
    )
    sleeps = Sleeps()

    await _client(transport, sleep=sleeps).query("sc_request", "")

    assert sleeps.calls == [2.0, 10.0]


async def test_retries_timeouts_and_gives_up_with_a_clear_error() -> None:
    timeout = httpx2.ReadTimeout("lento")
    transport, _ = _scripted(timeout, timeout, timeout)
    sleeps = Sleeps()

    with pytest.raises(ServiceNowError):
        await _client(transport, sleep=sleeps).query("sc_request", "")
    assert len(sleeps.calls) == 2


async def test_persistent_server_error_is_reported() -> None:
    transport, _ = _scripted(httpx2.Response(500), httpx2.Response(500), httpx2.Response(500))

    with pytest.raises(ServiceNowError):
        await _client(transport, sleep=Sleeps()).query("sc_request", "")


async def test_client_errors_are_not_retried() -> None:
    transport, seen = _scripted(httpx2.Response(400, json={"error": {"message": "Invalid"}}))

    with pytest.raises(ServiceNowError):
        await _client(transport, sleep=Sleeps()).query("sc_request", "")
    assert seen == ["token", "Bearer t1"]


async def test_revoked_token_is_replaced_once() -> None:
    transport, seen = _scripted(httpx2.Response(401), OK)

    await _client(transport, sleep=Sleeps()).query("sc_request", "")

    assert seen == ["token", "Bearer t1", "token", "Bearer t3"]


async def test_gives_up_if_every_token_is_rejected() -> None:
    transport, _ = _scripted(httpx2.Response(401), httpx2.Response(401), httpx2.Response(401))

    with pytest.raises(ServiceNowError):
        await _client(transport, sleep=Sleeps()).query("sc_request", "")
