"""Búsqueda semántica: primero se filtra por rol y solo después se ordena por similitud."""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel

from assistant.rag.embeddings import Embedder
from assistant.rag.store import VectorStore


class Fragment(BaseModel):
    titulo: str
    seccion: str
    texto: str
    score: float


class KnowledgeSearch:
    def __init__(self, embedder: Embedder, store: VectorStore) -> None:
        self._embedder = embedder
        self._store = store

    async def search(self, role: str, question: str, top_k: int) -> list[Fragment]:
        # Solo se cargan los fragmentos del rol: el resto nunca entra al ranking.
        candidates = [c for c in await self._store.chunks_for_role(role) if role in c.roles]
        if not candidates:
            return []
        query = await self._embedder.embed_query(question)
        scores = np.stack([c.vector for c in candidates]) @ query
        best = np.argsort(-scores, kind="stable")[:top_k]
        return [
            Fragment(
                titulo=candidates[i].titulo,
                seccion=candidates[i].seccion,
                texto=candidates[i].texto,
                score=round(float(scores[i]), 4),
            )
            for i in best
        ]
