"""RAG filtrado por rol: chunking, embeddings, índice en Redis y búsqueda."""

from assistant.rag.embeddings import Embedder, FastEmbedEmbedder
from assistant.rag.ingest import ensure_index, ingest
from assistant.rag.search import Fragment, KnowledgeSearch
from assistant.rag.store import VectorStore

__all__ = [
    "Embedder",
    "FastEmbedEmbedder",
    "Fragment",
    "KnowledgeSearch",
    "VectorStore",
    "ensure_index",
    "ingest",
]
