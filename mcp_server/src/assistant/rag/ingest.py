"""Indexa los documentos de empresa/ en Redis. Uso: python -m assistant.rag.ingest"""

from __future__ import annotations

import asyncio
import logging

import numpy as np

from assistant.company import Company, load_company
from assistant.config import Settings
from assistant.logs import configure_logging
from assistant.rag.chunking import chunk_document
from assistant.rag.embeddings import Embedder
from assistant.rag.store import VectorStore

logger = logging.getLogger(__name__)


async def ingest(company: Company, embedder: Embedder, store: VectorStore) -> int:
    chunks = [chunk for document in company.documentos for chunk in chunk_document(document)]
    if chunks:
        vectors = await embedder.embed_documents([chunk.embedding_text for chunk in chunks])
    else:
        vectors = np.empty((0, 0), dtype=np.float32)
    await store.replace_all(chunks, vectors, embedder.model_name)
    logger.info(
        "índice actualizado",
        extra={"fields": {"fragmentos": len(chunks), "modelo": embedder.model_name}},
    )
    return len(chunks)


async def ensure_index(company: Company, embedder: Embedder, store: VectorStore) -> None:
    """Indexa al arrancar si el índice está vacío o se creó con otro modelo."""
    if await store.count() == 0 or await store.indexed_model() != embedder.model_name:
        await ingest(company, embedder, store)


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
