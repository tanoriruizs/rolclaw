"""`make validate`: revisa empresa/ y todo lo que depende de ella, sin levantar nada."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from assistant.admin.openclaw_config import (
    AGENTS_DIR,
    OpenClawConfigError,
    demo_files,
)
from assistant.company import Company, CompanyConfigError, load_company

ENV_EXAMPLE = Path(".env.example")
COMPOSE = Path("docker-compose.yml")
CI = Path(".github/workflows/ci.yml")
# Variables por rol que debe recibir cada servicio de docker compose.
COMPOSE_VARS = {
    "mcp-server": ("ROLE_TOKEN_", "TELEGRAM_BOT_TOKEN_"),
    "openclaw": ("ROLE_TOKEN_", "TELEGRAM_BOT_TOKEN_"),
}


@dataclass(slots=True)
class Report:
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def validate(root: Path) -> Report:
    report = Report()
    try:
        company = load_company(root / "empresa")
    except CompanyConfigError as exc:
        report.problems.append(str(exc))
        return report

    _check_agent_folders(root, company, report)
    _check_env_example(root, company, report)
    _check_compose(root, company, report)
    _check_ci(root, company, report)
    _check_generated(root, company, report)
    _summarize(company, report)
    return report


def _check_agent_folders(root: Path, company: Company, report: Report) -> None:
    for role_id in company.roles:
        soul = AGENTS_DIR / role_id / "SOUL.md"
        if not (root / soul).is_file():
            report.problems.append(f"{soul}: falta la personalidad del agente del rol '{role_id}'")
    folders = {path.name for path in (root / AGENTS_DIR).glob("*/") if path.is_dir()}
    for extra in sorted(folders - company.roles.keys()):
        report.problems.append(f"{AGENTS_DIR / extra}: no hay ningún rol '{extra}' en roles.yaml")


def _check_env_example(root: Path, company: Company, report: Report) -> None:
    try:
        text = (root / ENV_EXAMPLE).read_text(encoding="utf-8")
    except FileNotFoundError:
        report.problems.append(f"{ENV_EXAMPLE}: el archivo no existe")
        return
    keys = {line.partition("=")[0].strip() for line in text.splitlines() if "=" in line}
    for variable in _role_variables(company, ("ROLE_TOKEN_", "TELEGRAM_BOT_TOKEN_")):
        if variable not in keys:
            report.problems.append(f"{ENV_EXAMPLE}: falta {variable}=")


def _check_compose(root: Path, company: Company, report: Report) -> None:
    services = _yaml(root, COMPOSE, report).get("services")
    if not isinstance(services, dict):
        report.problems.append(f"{COMPOSE}: no tiene servicios")
        return
    for service, prefixes in COMPOSE_VARS.items():
        environment = (services.get(service) or {}).get("environment") or {}
        for variable in _role_variables(company, prefixes):
            if variable not in environment:
                report.problems.append(f"{COMPOSE}: el servicio {service} no recibe {variable}")


def _check_ci(root: Path, company: Company, report: Report) -> None:
    if not (root / CI).is_file():
        return
    text = (root / CI).read_text(encoding="utf-8")
    for variable in _role_variables(company, ("TELEGRAM_BOT_TOKEN_",)):
        if variable not in text:
            report.problems.append(f"{CI}: falta {variable} (compose no arranca sin ella)")


def _check_generated(root: Path, company: Company, report: Report) -> None:
    try:
        expected = demo_files(root, company)
    except OpenClawConfigError as exc:
        report.problems.append(f"openclaw: {exc}")
        return
    for path, content in expected.items():
        current = (root / path).read_text(encoding="utf-8") if (root / path).is_file() else None
        if current != content:
            state = "no existe" if current is None else "está desactualizado"
            report.problems.append(f"{path}: {state}; ejecuta make openclaw-config")


def _summarize(company: Company, report: Report) -> None:
    report.summary.append(
        f"{len(company.roles)} roles · {len(company.empleados)} empleados · "
        f"{len(company.flujos)} flujos · {len(company.documentos)} documentos · "
        f"{len(company.catalogo.productos)} productos"
    )
    width = max(len(role_id) for role_id in company.roles)
    for role_id, role in company.roles.items():
        flows = sum(role_id in flow.roles for flow in company.flujos)
        documents = sum(role_id in document.roles for document in company.documentos)
        report.summary.append(
            f"  {role_id:<{width}}  {flows} flujos · {documents} documentos · "
            f"{len(role.herramientas)} herramientas · demo: {role.persona.nombre}"
        )
        if documents == 0:
            report.warnings.append(f"el rol '{role_id}' no puede leer ningún documento")


def _role_variables(company: Company, prefixes: tuple[str, ...]) -> list[str]:
    return [f"{prefix}{role_id.upper()}" for prefix in prefixes for role_id in company.roles]


def _yaml(root: Path, path: Path, report: Report) -> dict[str, object]:
    try:
        data = yaml.safe_load((root / path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        report.problems.append(f"{path}: el archivo no existe")
        return {}
    except yaml.YAMLError as exc:
        report.problems.append(f"{path}: YAML inválido ({exc})")
        return {}
    return data if isinstance(data, dict) else {}
