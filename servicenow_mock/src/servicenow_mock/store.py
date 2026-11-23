"""Persistencia en Redis: tokens OAuth con expiración y registros de la Table API."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

from redis.asyncio import Redis

READ_ONLY_FIELDS = frozenset({"sys_id", "number", "sys_created_on", "sys_updated_on"})
Record = dict[str, str]


def _now() -> str:
    # Formato de fecha que usa ServiceNow en sus respuestas.
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


class TokenStore:
    """Guarda solo el hash del token; Redis lo borra solo al expirar (TTL)."""

    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self._redis = redis
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _key(token: str) -> str:
        return f"sn:token:{hashlib.sha256(token.encode()).hexdigest()}"

    async def issue(self) -> str:
        token = secrets.token_urlsafe(32)
        await self._redis.set(self._key(token), "1", ex=self.ttl_seconds)
        return token

    async def is_valid(self, token: str) -> bool:
        return bool(await self._redis.exists(self._key(token)))


class RecordStore:
    def __init__(self, redis: Redis, table: str) -> None:
        self._redis = redis
        self._prefix = f"sn:{table}"
        self.table = table

    async def create(self, fields: Mapping[str, str]) -> Record:
        sequence = int(await self._redis.incr(f"{self._prefix}:seq"))
        now = _now()
        record = {k: v for k, v in fields.items() if k not in READ_ONLY_FIELDS}
        record |= {
            "sys_id": uuid.uuid4().hex,
            "number": f"REQ{10000 + sequence:07d}",
            "sys_created_on": now,
            "sys_updated_on": now,
        }
        pipe = self._redis.pipeline(transaction=True)
        pipe.set(f"{self._prefix}:{record['sys_id']}", json.dumps(record))
        pipe.zadd(f"{self._prefix}:index", {record["sys_id"]: sequence})
        await pipe.execute()
        return record

    async def get(self, sys_id: str) -> Record | None:
        raw = await self._redis.get(f"{self._prefix}:{sys_id}")
        return dict(json.loads(raw)) if raw is not None else None

    async def update(self, sys_id: str, fields: Mapping[str, str]) -> Record | None:
        record = await self.get(sys_id)
        if record is None:
            return None
        record |= {k: v for k, v in fields.items() if k not in READ_ONLY_FIELDS}
        record["sys_updated_on"] = _now()
        await self._redis.set(f"{self._prefix}:{sys_id}", json.dumps(record))
        return record

    async def all(self) -> list[Record]:
        ids = await self._redis.zrange(f"{self._prefix}:index", 0, -1)
        records = [await self.get(_text(sys_id)) for sys_id in ids]
        return [record for record in records if record is not None]


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
