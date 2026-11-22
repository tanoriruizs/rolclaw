"""Configuración de la empresa: roles, catálogo, empleados, flujos y documentos."""

from assistant.company.loader import CompanyConfigError, load_company
from assistant.company.models import (
    Company,
    Document,
    FieldType,
    Flow,
    Role,
    Rule,
    RuleAction,
    Tool,
)

__all__ = [
    "Company",
    "CompanyConfigError",
    "Document",
    "FieldType",
    "Flow",
    "Role",
    "Rule",
    "RuleAction",
    "Tool",
    "load_company",
]
