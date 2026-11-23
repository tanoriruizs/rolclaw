"""Modelos que validan la carpeta empresa/. Los campos se llaman igual que en los YAML."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]


class Tool(StrEnum):
    BUSCAR_CONOCIMIENTO = "buscar_conocimiento"
    LISTAR_FLUJOS = "listar_flujos"
    VER_FLUJO = "ver_flujo"
    INICIAR_SOLICITUD = "iniciar_solicitud"
    MIS_SOLICITUDES = "mis_solicitudes"
    CALCULAR = "calcular"
    PENDIENTES_DE_APROBAR = "pendientes_de_aprobar"
    APROBAR = "aprobar"
    RECHAZAR = "rechazar"
    VER_AUDITORIA = "ver_auditoria"


class FieldType(StrEnum):
    TEXTO = "texto"
    ENTERO = "entero"
    DECIMAL = "decimal"
    FECHA = "fecha"

    @property
    def is_numeric(self) -> bool:
        return self in {FieldType.ENTERO, FieldType.DECIMAL}


class RuleAction(StrEnum):
    RECHAZAR = "rechazar"
    REQUIERE_APROBACION = "requiere_aprobacion"


class _Model(BaseModel):
    # extra="forbid": un campo mal escrito en el YAML es un error, no se ignora.
    model_config = ConfigDict(extra="forbid", frozen=True)


class Persona(_Model):
    nombre: str
    empleado_id: str


class Role(_Model):
    nombre: str
    persona: Persona
    herramientas: frozenset[Tool] = Field(min_length=1)


class RolesFile(_Model):
    roles: dict[Identifier, Role] = Field(min_length=1)


class Product(_Model):
    id: str
    nombre: str
    categoria: str
    precio: Decimal = Field(gt=0)


class Catalog(_Model):
    moneda: str = Field(pattern=r"^[A-Z]{3}$")
    productos: tuple[Product, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_products(self) -> Self:
        _ensure_unique((p.id for p in self.productos), "producto")
        return self


class Employee(_Model):
    id: str
    nombre: str
    area: str
    puesto: str
    fecha_ingreso: date
    jefe: str | None = None


class EmployeesFile(_Model):
    empleados: tuple[Employee, ...] = Field(min_length=1)


class RequiredField(_Model):
    campo: Identifier
    tipo: FieldType
    min: Decimal | None = None
    max: Decimal | None = None

    @model_validator(mode="after")
    def _check_limits(self) -> Self:
        has_limits = self.min is not None or self.max is not None
        if has_limits and not self.tipo.is_numeric:
            raise ValueError(f"'{self.campo}': min y max solo aplican a campos numéricos")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"'{self.campo}': min no puede ser mayor que max")
        return self


class Rule(_Model):
    campo: Identifier
    mayor_que: Decimal | None = None
    menor_que: Decimal | None = None
    accion: RuleAction
    aprueba: Identifier | None = None
    mensaje: str | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        prefix = f"regla de '{self.campo}'"
        if (self.mayor_que is None) == (self.menor_que is None):
            raise ValueError(f"{prefix}: usa exactamente una condición (mayor_que o menor_que)")
        needs_approver = self.accion is RuleAction.REQUIERE_APROBACION
        if needs_approver and self.aprueba is None:
            raise ValueError(f"{prefix}: requiere_aprobacion necesita 'aprueba'")
        if not needs_approver and self.aprueba is not None:
            raise ValueError(f"{prefix}: 'aprueba' solo aplica a requiere_aprobacion")
        if self.accion is RuleAction.RECHAZAR and not self.mensaje:
            raise ValueError(f"{prefix}: rechazar necesita 'mensaje'")
        return self


class Flow(_Model):
    id: Identifier
    nombre: str
    roles: frozenset[Identifier] = Field(min_length=1)
    descripcion: str
    datos_requeridos: tuple[RequiredField, ...] = Field(min_length=1)
    pasos: tuple[str, ...] = Field(min_length=1)
    reglas: tuple[Rule, ...] = ()

    @model_validator(mode="after")
    def _check_rules_use_known_numeric_fields(self) -> Self:
        _ensure_unique((d.campo for d in self.datos_requeridos), "campo")
        types = {d.campo: d.tipo for d in self.datos_requeridos}
        for rule in self.reglas:
            field_type = types.get(rule.campo)
            if field_type is None:
                raise ValueError(f"la regla usa '{rule.campo}', que no está en datos_requeridos")
            if not field_type.is_numeric:
                raise ValueError(f"la regla de '{rule.campo}' necesita un campo numérico")
        return self


class Document(_Model):
    archivo: str
    titulo: str
    roles: frozenset[Identifier] = Field(min_length=1)
    contenido: str = Field(min_length=1)


class Company(_Model):
    roles: dict[Identifier, Role]
    catalogo: Catalog
    empleados: tuple[Employee, ...]
    flujos: tuple[Flow, ...]
    documentos: tuple[Document, ...]

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        self._check_employees()
        self._check_flows()
        for document in self.documentos:
            self._check_known_roles(document.roles, f"documento '{document.archivo}'")
        return self

    def _check_employees(self) -> None:
        _ensure_unique((e.id for e in self.empleados), "empleado")
        names = {e.id: e.nombre for e in self.empleados}
        for employee in self.empleados:
            if employee.jefe is not None and employee.jefe not in names:
                raise ValueError(f"empleado '{employee.id}': el jefe '{employee.jefe}' no existe")
        for role_id, role in self.roles.items():
            persona = role.persona
            if names.get(persona.empleado_id) != persona.nombre:
                raise ValueError(
                    f"rol '{role_id}': la persona no coincide con el empleado {persona.empleado_id}"
                )

    def _check_flows(self) -> None:
        _ensure_unique((f.id for f in self.flujos), "flujo")
        for flow in self.flujos:
            where = f"flujo '{flow.id}'"
            self._check_known_roles(flow.roles, where)
            for role_id in flow.roles:
                if Tool.INICIAR_SOLICITUD not in self.roles[role_id].herramientas:
                    raise ValueError(f"{where}: el rol '{role_id}' no puede iniciar solicitudes")
            for rule in flow.reglas:
                if rule.aprueba is None:
                    continue
                self._check_known_roles({rule.aprueba}, where)
                if Tool.APROBAR not in self.roles[rule.aprueba].herramientas:
                    raise ValueError(f"{where}: el rol '{rule.aprueba}' no puede aprobar")

    def _check_known_roles(self, roles: Iterable[str], where: str) -> None:
        unknown = sorted(set(roles) - self.roles.keys())
        if unknown:
            raise ValueError(f"{where}: roles desconocidos {unknown}")


def _ensure_unique(values: Iterable[str], what: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"{what} duplicado: '{value}'")
        seen.add(value)
