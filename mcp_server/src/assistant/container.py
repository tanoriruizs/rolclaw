"""Construye los servicios una sola vez; las rutas los reciben por inyección de dependencias."""

from __future__ import annotations

from dataclasses import dataclass

from assistant.auth import TokenAuthenticator
from assistant.company import Company, load_company
from assistant.config import Settings
from assistant.flows import FlowCatalog
from assistant.permissions import Permissions


@dataclass(frozen=True, slots=True)
class Services:
    company: Company
    auth: TokenAuthenticator
    permissions: Permissions
    flows: FlowCatalog


def build_services(settings: Settings) -> Services:
    company = load_company(settings.empresa_dir)
    return Services(
        company=company,
        auth=TokenAuthenticator(settings.role_tokens, company),
        permissions=Permissions(company.roles),
        flows=FlowCatalog(company.flujos),
    )
