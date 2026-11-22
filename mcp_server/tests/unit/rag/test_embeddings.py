from collections.abc import Iterator

import numpy as np
import pytest

from assistant.rag.embeddings import FastEmbedEmbedder, normalize


def test_normalize_makes_unit_vectors_and_keeps_zero_vectors() -> None:
    vectors = normalize(np.array([[3.0, 4.0], [0.0, 0.0]]))

    assert vectors.dtype == np.float32
    np.testing.assert_allclose(vectors, [[0.6, 0.8], [0.0, 0.0]])


class FakeTextEmbedding:
    """Imita la interfaz de fastembed.TextEmbedding sin descargar el modelo."""

    instances = 0

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        FakeTextEmbedding.instances += 1
        self.model_name = model_name
        self.cache_dir = cache_dir

    def passage_embed(self, texts: list[str]) -> Iterator[np.ndarray]:
        return (np.array([len(t), 0.0]) for t in texts)

    def query_embed(self, texts: list[str]) -> Iterator[np.ndarray]:
        return (np.array([0.0, len(t)]) for t in texts)


async def test_fastembed_embedder_loads_model_once_and_normalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("fastembed.TextEmbedding", FakeTextEmbedding)
    FakeTextEmbedding.instances = 0
    embedder = FastEmbedEmbedder("modelo-x")

    documents = await embedder.embed_documents(["uno", "dos"])
    query = await embedder.embed_query("pregunta")

    assert embedder.model_name == "modelo-x"
    assert FakeTextEmbedding.instances == 1
    np.testing.assert_allclose(documents, [[1.0, 0.0], [1.0, 0.0]])
    np.testing.assert_allclose(query, [0.0, 1.0])
