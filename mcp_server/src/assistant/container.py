"""Construye los servicios una sola vez; las rutas los reciben por inyección de dependencias."""

from __future__ import annotations

from dataclasses import dataclass

import httpx2
from redis.asyncio import Redis

from assistant.audit import AuditLog
from assistant.auth import TokenAuthenticator
from assistant.company import Company, load_company
from assistant.config import Settings
from assistant.errors import ConfigurationError
from assistant.flows import FlowCatalog
from assistant.permissions import Permissions
from assistant.rag import Embedder, FastEmbedEmbedder, KnowledgeSearch, VectorStore
from assistant.requests_service import RequestsService
from assistant.servicenow_client import ServiceNowClient


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
    servicenow: ServiceNowClient
    requests: RequestsService
    audit: AuditLog


def build_services(
    settings: Settings,
    *,
    redis: Redis | None = None,
    embedder: Embedder | None = None,
    servicenow: ServiceNowClient | None = None,
) -> Services:
    """redis, embedder y servicenow se pueden inyectar (los tests usan versiones locales)."""
    company = load_company(settings.empresa_dir)
    auth = TokenAuthenticator(settings.role_tokens, company)  # primero lo más básico
    redis = redis or create_redis(settings)
    embedder = embedder or create_embedder(settings)
    servicenow = servicenow or create_servicenow(settings)
    store = VectorStore(redis)
    return Services(
        company=company,
        auth=auth,
        permissions=Permissions(company.roles),
        flows=FlowCatalog(company.flujos),
        redis=redis,
        embedder=embedder,
        store=store,
        knowledge=KnowledgeSearch(embedder, store),
        servicenow=servicenow,
        requests=RequestsService(company, servicenow),
        audit=AuditLog(redis),
    )


def create_redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url)


def create_embedder(settings: Settings) -> Embedder:
    return FastEmbedEmbedder(settings.embedding_model, settings.embedding_cache_dir)


def create_servicenow(settings: Settings) -> ServiceNowClient:
    if settings.servicenow_client_secret is None:
        raise ConfigurationError("Falta SERVICENOW_CLIENT_SECRET. Ejecuta make tokens.")
    http = httpx2.AsyncClient(timeout=httpx2.Timeout(10.0, connect=5.0))
    return ServiceNowClient(
        http,
        settings.servicenow_base_url,
        settings.servicenow_client_id,
        settings.servicenow_client_secret,
    )
