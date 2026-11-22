"""Construye los servicios una sola vez; las rutas los reciben por inyección de dependencias."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from assistant.auth import TokenAuthenticator
from assistant.company import Company, load_company
from assistant.config import Settings
from assistant.flows import FlowCatalog
from assistant.permissions import Permissions
from assistant.rag import Embedder, FastEmbedEmbedder, KnowledgeSearch, VectorStore


@dataclass(frozen=True, slots=True)
class Services:
    company: Company
    auth: TokenAuthenticator
    permissions: Permissions
    flows: FlowCatalog
    redis: Redis
    embedder: Embedder
    store: VectorStore
    knowledge: KnowledgeSearch


def build_services(
    settings: Settings, *, redis: Redis | None = None, embedder: Embedder | None = None
) -> Services:
    """redis y embedder se pueden inyectar (los tests usan versiones falsas)."""
    company = load_company(settings.empresa_dir)
    redis = redis or create_redis(settings)
    embedder = embedder or create_embedder(settings)
    store = VectorStore(redis)
    return Services(
        company=company,
        auth=TokenAuthenticator(settings.role_tokens, company),
        permissions=Permissions(company.roles),
        flows=FlowCatalog(company.flujos),
        redis=redis,
        embedder=embedder,
        store=store,
        knowledge=KnowledgeSearch(embedder, store),
    )


def create_redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url)


def create_embedder(settings: Settings) -> Embedder:
    return FastEmbedEmbedder(settings.embedding_model, settings.embedding_cache_dir)
