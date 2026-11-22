"""Índice de fragmentos en Redis: un hash por fragmento y un set de ids por rol."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from redis.asyncio import Redis

from assistant.rag.chunking import Chunk
from assistant.rag.embeddings import Vectors


@dataclass(frozen=True, slots=True)
class StoredChunk:
    id: str
    titulo: str
    seccion: str
    texto: str
    roles: frozenset[str]
    vector: Vectors


class VectorStore:
    def __init__(self, redis: Redis, prefix: str = "rag") -> None:
        self._redis = redis
        self._prefix = prefix

    def _key(self, *parts: str) -> str:
        return ":".join((self._prefix, *parts))

    async def replace_all(self, chunks: Sequence[Chunk], vectors: Vectors, model: str) -> None:
        """Reemplaza el índice completo en una sola transacción (nadie ve un índice a medias)."""
        old_ids = await self._redis.smembers(self._key("ids"))
        old_roles = await self._redis.smembers(self._key("roles"))
        roles = sorted({role for chunk in chunks for role in chunk.roles})

        pipe = self._redis.pipeline(transaction=True)
        for old_id in old_ids:
            pipe.delete(self._key("chunk", _text(old_id)))
        for old_role in old_roles:
            pipe.delete(self._key("role", _text(old_role)))
        pipe.delete(self._key("ids"), self._key("roles"), self._key("meta"))
        for chunk, vector in zip(chunks, vectors, strict=True):
            pipe.hset(
                self._key("chunk", chunk.id),
                mapping={
                    "titulo": chunk.titulo,
                    "seccion": chunk.seccion,
                    "texto": chunk.texto,
                    "roles": json.dumps(sorted(chunk.roles)),
                    "vector": vector.astype(np.float32).tobytes(),
                },
            )
            pipe.sadd(self._key("ids"), chunk.id)
            for role in chunk.roles:
                pipe.sadd(self._key("role", role), chunk.id)
        if roles:
            pipe.sadd(self._key("roles"), *roles)
        pipe.hset(self._key("meta"), mapping={"model": model, "count": len(chunks)})
        await pipe.execute()

    async def count(self) -> int:
        return int(await self._redis.scard(self._key("ids")))

    async def indexed_model(self) -> str | None:
        model = await self._redis.hget(self._key("meta"), "model")
        return _text(model) if model is not None else None

    async def chunks_for_role(self, role: str) -> list[StoredChunk]:
        ids = sorted(_text(m) for m in await self._redis.smembers(self._key("role", role)))
        pipe = self._redis.pipeline(transaction=False)
        for chunk_id in ids:
            pipe.hgetall(self._key("chunk", chunk_id))
        rows: list[dict[bytes, bytes]] = await pipe.execute()
        return [_decode(chunk_id, row) for chunk_id, row in zip(ids, rows, strict=True) if row]


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


def _decode(chunk_id: str, row: dict[bytes, bytes]) -> StoredChunk:
    return StoredChunk(
        id=chunk_id,
        titulo=row[b"titulo"].decode(),
        seccion=row[b"seccion"].decode(),
        texto=row[b"texto"].decode(),
        roles=frozenset(json.loads(row[b"roles"])),
        vector=np.frombuffer(row[b"vector"], dtype=np.float32),
    )
