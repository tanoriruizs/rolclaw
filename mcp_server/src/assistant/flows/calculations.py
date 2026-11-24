"""Cálculos que hace el servidor con datos de la empresa (precios, calendario, antigüedad).

El agente nunca envía estos valores: así no puede equivocarse ni inventarlos.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from assistant.company import Company
from assistant.company.models import Calculation, Calendar
from assistant.errors import InvalidInputError

MAX_PERIOD_DAYS = 366
CENT = Decimal("0.01")

FieldValue = str | int | Decimal | date


@dataclass(frozen=True, slots=True)
class FlowContext:
    """Lo que el motor necesita saber de la empresa y del solicitante."""

    precios: Mapping[str, Decimal]
    empleados: frozenset[str]
    calendario: Calendar
    fecha_ingreso: date | None = None
    dias_vacaciones_usados: int = 0

    @classmethod
    def from_company(cls, company: Company) -> FlowContext:
        return cls(
            precios={p.id.upper(): p.precio for p in company.catalogo.productos},
            empleados=frozenset(e.id.upper() for e in company.empleados),
            calendario=company.calendario,
        )


def business_days(start: date, end: date, holidays: frozenset[date]) -> int:
    """Días de lunes a viernes entre dos fechas (ambas incluidas), sin contar festivos."""
    if end < start:
        raise InvalidInputError("Datos inválidos: la fecha final es anterior a la inicial.")
    if (end - start).days >= MAX_PERIOD_DAYS:
        raise InvalidInputError("Datos inválidos: el periodo no puede ser mayor a un año.")
    days = (start + timedelta(offset) for offset in range((end - start).days + 1))
    return sum(1 for day in days if day.weekday() < 5 and day not in holidays)


def add_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 de febrero en un año que no es bisiesto
        return day.replace(year=day.year + years, day=28)


def years_of_service(hired: date, on: date) -> int:
    years = on.year - hired.year
    return max(years - 1 if add_years(hired, years) > on else years, 0)


def service_year(hired: date, on: date) -> tuple[date, date]:
    """Año de servicio (de aniversario a aniversario) en el que cae la fecha."""
    start = add_years(hired, years_of_service(hired, on))
    return start, add_years(start, 1)


def _hired(context: FlowContext) -> date:
    if context.fecha_ingreso is None:
        raise InvalidInputError("No se encontró tu fecha de ingreso; contacta a Recursos Humanos.")
    return context.fecha_ingreso


def as_number(value: FieldValue) -> Decimal:
    if isinstance(value, int | Decimal) and not isinstance(value, bool):
        return Decimal(value)
    raise TypeError(f"valor no numérico en un cálculo: {value!r}")


def as_date(value: FieldValue) -> date:
    if isinstance(value, date):
        return value
    raise TypeError(f"valor que no es fecha en un cálculo: {value!r}")


def _unit_price(context: FlowContext, args: Mapping[str, FieldValue]) -> Decimal:
    return context.precios[str(args["producto"])]


def _discounted_total(context: FlowContext, args: Mapping[str, FieldValue]) -> Decimal:
    gross = _unit_price(context, args) * as_number(args["cantidad"])
    total = gross * (Decimal(100) - as_number(args["descuento_pct"])) / Decimal(100)
    return total.quantize(CENT, rounding=ROUND_HALF_UP)


def _business_days(context: FlowContext, args: Mapping[str, FieldValue]) -> int:
    return business_days(
        as_date(args["inicio"]), as_date(args["fin"]), context.calendario.dias_festivos
    )


def _entitled_days(context: FlowContext, args: Mapping[str, FieldValue]) -> int:
    years = years_of_service(_hired(context), as_date(args["inicio"]))
    return context.calendario.vacation_days(years)


def _vacation_balance(context: FlowContext, args: Mapping[str, FieldValue]) -> int:
    """Días que quedarían después de esta solicitud (negativo si no alcanzan)."""
    used = context.dias_vacaciones_usados + int(as_number(args["dias"]))
    return _entitled_days(context, args) - used


CALCULATIONS: dict[Calculation, Callable[[FlowContext, Mapping[str, FieldValue]], FieldValue]] = {
    Calculation.PRECIO_PRODUCTO: _unit_price,
    Calculation.TOTAL_CON_DESCUENTO: _discounted_total,
    Calculation.DIAS_HABILES: _business_days,
    Calculation.DIAS_POR_ANTIGUEDAD: _entitled_days,
    Calculation.SALDO_VACACIONES: _vacation_balance,
}
