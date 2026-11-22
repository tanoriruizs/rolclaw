"""Lee la carpeta empresa/ y la valida con los modelos."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from assistant.company.models import Catalog, Company, Document, EmployeesFile, Flow, RolesFile

FRONT_MATTER_DELIMITER = "---\n"


class CompanyConfigError(Exception):
    """La configuración de la empresa es inválida; el mensaje indica el archivo."""


def load_company(root: Path) -> Company:
    roles = _load_model(RolesFile, _read_yaml(root / "roles.yaml"), root / "roles.yaml")
    employees = _load_model(
        EmployeesFile, _read_yaml(root / "empleados.yaml"), root / "empleados.yaml"
    )
    catalog = _load_model(Catalog, _read_yaml(root / "catalogo.yaml"), root / "catalogo.yaml")
    flows = [_load_model(Flow, _read_yaml(p), p) for p in sorted(root.glob("flujos/**/*.yaml"))]
    documents = [_load_document(p) for p in sorted(root.glob("documentos/*.md"))]

    data = {
        "roles": roles.roles,
        "catalogo": catalog,
        "empleados": employees.empleados,
        "flujos": flows,
        "documentos": documents,
    }
    return _load_model(Company, data, root)


def _load_document(path: Path) -> Document:
    parts = _read_text(path).split(FRONT_MATTER_DELIMITER, 2)
    if len(parts) != 3 or parts[0].strip():
        raise CompanyConfigError(f"{path}: falta el front matter entre líneas '---'")
    _, front_matter, body = parts
    metadata = _parse_yaml(front_matter, path)
    if not isinstance(metadata, dict):
        raise CompanyConfigError(f"{path}: el front matter debe tener titulo y roles")
    return _load_model(
        Document, {**metadata, "archivo": path.name, "contenido": body.strip()}, path
    )


def _load_model[M: BaseModel](model: type[M], data: object, path: Path) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise CompanyConfigError(f"{path}: {_describe(exc)}") from exc


def _describe(exc: ValidationError) -> str:
    problems: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(raíz)"
        problems.append(f"{location}: {error['msg']}")
    return "; ".join(problems)


def _read_yaml(path: Path) -> object:
    return _parse_yaml(_read_text(path), path)


def _parse_yaml(text: str, path: Path) -> object:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CompanyConfigError(f"{path}: YAML inválido ({exc})") from exc


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CompanyConfigError(f"{path}: el archivo no existe") from exc
