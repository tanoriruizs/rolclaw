"""Indexa los documentos de empresa/ en Redis. Uso: python -m assistant.rag.ingest"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Sequence

import numpy as np

from assistant.company import Company, load_company
from assistant.config import Settings
from assistant.logs import configure_logging
from assistant.rag.chunking import Chunk, chunk_document
from assistant.rag.embeddings import Embedder
from assistant.rag.store import VectorStore

logger = logging.getLogger(__name__)


def build_chunks(company: Company) -> list[Chunk]:
    return [chunk for document in company.documentos for chunk in chunk_document(document)]


def fingerprint(chunks: Sequence[Chunk], model_name: str) -> str:
    """Huella del contenido, los roles y el modelo: si algo cambia, hay que re-indexar."""
    digest = hashlib.sha256(model_name.encode())
    for chunk in chunks:
        row = [chunk.id, chunk.embedding_text, sorted(chunk.roles)]
        digest.update(json.dumps(row, ensure_ascii=False).encode())
    return digest.hexdigest()


async def ingest(company: Company, embedder: Embedder, store: VectorStore) -> int:
    chunks = build_chunks(company)
    if chunks:
        vectors = await embedder.embed_documents([chunk.embedding_text for chunk in chunks])
    else:
        vectors = np.empty((0, 0), dtype=np.float32)
    await store.replace_all(
        chunks, vectors, embedder.model_name, fingerprint(chunks, embedder.model_name)
    )
    logger.info(
        "índice actualizado",
        extra={"fields": {"fragmentos": len(chunks), "modelo": embedder.model_name}},
    )
    return len(chunks)


async def ensure_index(company: Company, embedder: Embedder, store: VectorStore) -> bool:
    """Re-indexa al arrancar si el índice está vacío o los documentos, roles o modelo cambiaron."""
    expected = fingerprint(build_chunks(company), embedder.model_name)
    if await store.count() > 0 and await store.indexed_fingerprint() == expected:
        return False
    await ingest(company, embedder, store)
    return True


async def _main() -> None:
    from assistant.container import create_embedder, create_redis

    settings = Settings.from_environ()
    configure_logging(settings.log_level)
    redis = create_redis(settings)
    try:
        await ingest(
            load_company(settings.empresa_dir), create_embedder(settings), VectorStore(redis)
        )
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(_main())
