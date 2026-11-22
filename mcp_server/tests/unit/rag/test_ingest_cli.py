from pathlib import Path

import pytest
from conftest import HashingEmbedder
from fakeredis import FakeAsyncRedis, FakeServer

from assistant import container
from assistant.rag import VectorStore
from assistant.rag.ingest import _main


async def test_make_ingest_indexes_documents(
    empresa_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = FakeServer()
    monkeypatch.setenv("EMPRESA_DIR", str(empresa_dir))
    monkeypatch.setattr(container, "create_redis", lambda _: FakeAsyncRedis(server=server))
    monkeypatch.setattr(container, "create_embedder", lambda _: HashingEmbedder())

    await _main()

    assert await VectorStore(FakeAsyncRedis(server=server)).count() > 0
