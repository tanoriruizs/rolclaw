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
    CONSULTAR_CATALOGO = "consultar_catalogo"
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
    PRODUCTO = "producto"  # un id que debe existir en el catálogo
    EMPLEADO = "empleado"  # un id que debe existir en empleados.yaml

    @property
    def is_numeric(self) -> bool:
        return self in {FieldType.ENTERO, FieldType.DECIMAL}


class Calculation(StrEnum):
    """Valores que calcula el servidor; el agente no los puede enviar."""

    PRECIO_PRODUCTO = "precio_producto"
    TOTAL_CON_DESCUENTO = "total_con_descuento"
    DIAS_HABILES = "dias_habiles"
    DIAS_POR_ANTIGUEDAD = "dias_por_antiguedad"
    SALDO_VACACIONES = "saldo_vacaciones"

    @property
    def output(self) -> FieldType:
        decimals = {Calculation.PRECIO_PRODUCTO, Calculation.TOTAL_CON_DESCUENTO}
        return FieldType.DECIMAL if self in decimals else FieldType.ENTERO


_NUMERIC = frozenset({FieldType.ENTERO, FieldType.DECIMAL})
_DATE = frozenset({FieldType.FECHA})
_PRODUCT = frozenset({FieldType.PRODUCTO})
# Argumentos de cada cálculo: nombre -> tipos de campo que acepta.
CALCULATION_ARGS: dict[Calculation, dict[str, frozenset[FieldType]]] = {
    Calculation.PRECIO_PRODUCTO: {"producto": _PRODUCT},
    Calculation.TOTAL_CON_DESCUENTO: {
        "producto": _PRODUCT,
        "cantidad": _NUMERIC,
        "descuento_pct": _NUMERIC,
    },
    Calculation.DIAS_HABILES: {"inicio": _DATE, "fin": _DATE},
    Calculation.DIAS_POR_ANTIGUEDAD: {"inicio": _DATE},
    Calculation.SALDO_VACACIONES: {"inicio": _DATE, "dias": _NUMERIC},
}


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


class VacationTier(_Model):
    desde: int = Field(ge=1)  # años de servicio cumplidos
    dias: int = Field(ge=0)


class Calendar(_Model):
    dias_festivos: frozenset[date] = frozenset()
    vacaciones_por_antiguedad: tuple[VacationTier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _tiers_in_order(self) -> Self:
        years = [tier.desde for tier in self.vacaciones_por_antiguedad]
        if years != sorted(set(years)):
            raise ValueError("vacaciones_por_antiguedad debe ir en orden y sin repetir 'desde'")
        return self

    def vacation_days(self, years_of_service: int) -> int:
        """Días que corresponden con esos años cumplidos (0 si aún no cumple el primero)."""
        days = 0
        for tier in self.vacaciones_por_antiguedad:
            if years_of_service >= tier.desde:
                days = tier.dias
        return days


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


class Calculated(_Model):
    campo: Identifier
    calculo: Calculation
    con: dict[Identifier, Identifier]  # argumento del cálculo -> campo del flujo

    @model_validator(mode="after")
    def _check_arguments(self) -> Self:
        expected = set(CALCULATION_ARGS[self.calculo])
        if set(self.con) != expected:
            raise ValueError(
                f"'{self.campo}': el cálculo {self.calculo} necesita 'con' {sorted(expected)}"
            )
        return self


class Flow(_Model):
    id: Identifier
    nombre: str
    roles: frozenset[Identifier] = Field(min_length=1)
    descripcion: str
    datos_requeridos: tuple[RequiredField, ...] = Field(min_length=1)
    calculados: tuple[Calculated, ...] = ()
    pasos: tuple[str, ...] = Field(min_length=1)
    reglas: tuple[Rule, ...] = ()

    @model_validator(mode="after")
    def _check_fields(self) -> Self:
        fields = [d.campo for d in self.datos_requeridos] + [c.campo for c in self.calculados]
        _ensure_unique(fields, "campo")
        types = {d.campo: d.tipo for d in self.datos_requeridos}
        # Cada cálculo solo puede usar campos definidos antes que él.
        for calculated in self.calculados:
            for argument, field in calculated.con.items():
                field_type = types.get(field)
                if field_type is None:
                    raise ValueError(
                        f"'{calculated.campo}' usa '{field}', que no está definido antes"
                    )
                if field_type not in CALCULATION_ARGS[calculated.calculo][argument]:
                    raise ValueError(
                        f"'{calculated.campo}': '{field}' no sirve como '{argument}' "
                        f"(es de tipo {field_type})"
                    )
            types[calculated.campo] = calculated.calculo.output
        for rule in self.reglas:
            field_type = types.get(rule.campo)
            if field_type is None:
                raise ValueError(f"la regla usa '{rule.campo}', que no es un campo del flujo")
            if not field_type.is_numeric:
                raise ValueError(f"la regla de '{rule.campo}' necesita un campo numérico")
        return self

    def calculation(self, kind: Calculation) -> Calculated | None:
        return next((c for c in self.calculados if c.calculo is kind), None)


class Document(_Model):
    archivo: str
    titulo: str
    roles: frozenset[Identifier] = Field(min_length=1)
    contenido: str = Field(min_length=1)


class Company(_Model):
    roles: dict[Identifier, Role]
    catalogo: Catalog
    empleados: tuple[Employee, ...]
    calendario: Calendar
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
