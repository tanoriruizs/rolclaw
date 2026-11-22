"""Embeddings detrás de una interfaz para poder cambiar a Azure OpenAI sin tocar el resto."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:
    from fastembed import TextEmbedding

Vectors = NDArray[np.float32]


class Embedder(Protocol):
    @property
    def model_name(self) -> str: ...

    async def embed_documents(self, texts: Sequence[str]) -> Vectors: ...

    async def embed_query(self, text: str) -> Vectors: ...


def normalize(vectors: NDArray[np.floating]) -> Vectors:
    """Vectores de largo 1: así la similitud coseno es un simple producto punto."""
    matrix = np.atleast_2d(np.asarray(vectors, dtype=np.float32))
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    normalized: Vectors = np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)
    return normalized


class FastEmbedEmbedder:
    """Modelo local (ONNX en CPU). Se carga la primera vez que se usa, en un hilo aparte."""

    def __init__(self, model_name: str, cache_dir: Path | None = None) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._model: TextEmbedding | None = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    async def embed_documents(self, texts: Sequence[str]) -> Vectors:
        return await asyncio.to_thread(self._embed, list(texts), query=False)

    async def embed_query(self, text: str) -> Vectors:
        vectors = await asyncio.to_thread(self._embed, [text], query=True)
        query: Vectors = vectors[0]
        return query

    def _embed(self, texts: list[str], *, query: bool) -> Vectors:
        model = self._load()
        embeddings = model.query_embed(texts) if query else model.passage_embed(texts)
        return normalize(np.array(list(embeddings)))

    def _load(self) -> TextEmbedding:
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding  # import pesado: solo cuando se necesita

                cache = str(self._cache_dir) if self._cache_dir else None
                self._model = TextEmbedding(self._model_name, cache_dir=cache)
            return self._model
