"""App del ServiceNow simulado: POST /oauth_token.do y /api/now/table/sc_request."""

from __future__ import annotations

import hmac
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis

from servicenow_mock.store import Record, RecordStore, TokenStore

TABLE = "sc_request"


class MockSettings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True)

    redis_url: str = "redis://localhost:6379/1"
    servicenow_client_id: str = "cardinal-assistant"
    servicenow_client_secret: SecretStr
    token_ttl_seconds: int = 1800


def _failure(status: int, message: str, detail: str = "") -> JSONResponse:
    """Errores con la misma forma que la Table API real."""
    body = {"error": {"message": message, "detail": detail}, "status": "failure"}
    return JSONResponse(body, status_code=status)


def create_app(settings: MockSettings | None = None, *, redis: Redis | None = None) -> FastAPI:
    settings = settings or MockSettings()  # type: ignore[call-arg]
    redis = redis or Redis.from_url(settings.redis_url)
    tokens = TokenStore(redis, settings.token_ttl_seconds)
    records = RecordStore(redis, TABLE)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await redis.aclose()

    app = FastAPI(title="ServiceNow simulado", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        await redis.ping()
        return {"status": "ok"}

    @app.post("/oauth_token.do")
    async def oauth_token(request: Request) -> JSONResponse:
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
        if form.get("grant_type") != "client_credentials":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
        valid_id = hmac.compare_digest(
            form.get("client_id", "").encode(), settings.servicenow_client_id.encode()
        )
        valid_secret = hmac.compare_digest(
            form.get("client_secret", "").encode(),
            settings.servicenow_client_secret.get_secret_value().encode(),
        )
        if not (valid_id and valid_secret):
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        token = await tokens.issue()
        return JSONResponse(
            {
                "access_token": token,
                "token_type": "Bearer",
                "expires_in": settings.token_ttl_seconds,
                "scope": "useraccount",
            }
        )

    @app.middleware("http")
    async def require_token(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path.startswith("/api/"):
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not await tokens.is_valid(token):
                return _failure(
                    401, "User Not Authenticated", "Required to provide Auth information"
                )
        return await call_next(request)

    @app.post(f"/api/now/table/{TABLE}")
    async def create_record(request: Request) -> JSONResponse:
        fields = await _fields(request)
        if isinstance(fields, JSONResponse):
            return fields
        return JSONResponse({"result": await records.create(fields)}, status_code=201)

    @app.get(f"/api/now/table/{TABLE}")
    async def query_records(sysparm_query: str = "", sysparm_limit: int = 100) -> JSONResponse:
        try:
            matches = filter_records(await records.all(), sysparm_query)
        except ValueError as exc:
            return _failure(400, "Invalid query", str(exc))
        return JSONResponse({"result": matches[: max(sysparm_limit, 0)]})

    @app.get(f"/api/now/table/{TABLE}/{{sys_id}}")
    async def get_record(sys_id: str) -> JSONResponse:
        record = await records.get(sys_id)
        if record is None:
            return _failure(404, "No Record found", "Record doesn't exist or ACL restricts it")
        return JSONResponse({"result": record})

    @app.patch(f"/api/now/table/{TABLE}/{{sys_id}}")
    async def update_record(sys_id: str, request: Request) -> JSONResponse:
        fields = await _fields(request)
        if isinstance(fields, JSONResponse):
            return fields
        record = await records.update(sys_id, fields)
        if record is None:
            return _failure(404, "No Record found", "Record doesn't exist or ACL restricts it")
        return JSONResponse({"result": record})

    return app


async def _fields(request: Request) -> dict[str, str] | JSONResponse:
    """Como la API real, todos los valores se guardan como texto."""
    try:
        body = json.loads(await request.body())
    except ValueError:
        return _failure(400, "Invalid JSON")
    if not isinstance(body, dict) or any(isinstance(v, dict | list) for v in body.values()):
        return _failure(400, "Invalid body", "Expected an object with scalar values")
    return {str(k): "" if v is None else str(v) for k, v in body.items()}


def filter_records(records: list[Record], query: str) -> list[Record]:
    """Subconjunto de sysparm_query: =, !=, ORDERBY y ORDERBYDESC, unidos por ^."""
    order: tuple[str, bool] | None = None
    for part in filter(None, query.split("^")):
        if part.startswith("ORDERBYDESC"):
            order = (part.removeprefix("ORDERBYDESC"), True)
        elif part.startswith("ORDERBY"):
            order = (part.removeprefix("ORDERBY"), False)
        elif "!=" in part:
            field, value = part.split("!=", 1)
            records = [r for r in records if r.get(field, "") != value]
        elif "=" in part:
            field, value = part.split("=", 1)
            records = [r for r in records if r.get(field, "") == value]
        else:
            raise ValueError(f"operador no soportado en '{part}'")
    if order is not None:
        field, descending = order
        records = sorted(records, key=lambda r: r.get(field, ""), reverse=descending)
    return records
