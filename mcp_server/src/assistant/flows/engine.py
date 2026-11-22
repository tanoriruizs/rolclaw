"""Motor de flujos: valida los datos de una solicitud y aplica las reglas del YAML.

Es una función pura (sin red ni base de datos): recibe el flujo y los datos y
devuelve la decisión. Las reglas se evalúan en el servidor; el modelo no decide.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from assistant.company import FieldType, Flow, Rule, RuleAction
from assistant.company.models import RequiredField
from assistant.errors import InvalidInputError

MAX_TEXT_CHARS = 500
_INTEGER = re.compile(r"^-?\d+$")

FieldValue = str | int | Decimal | date


class Outcome(StrEnum):
    RECHAZADA = "rechazada"
    PENDIENTE_APROBACION = "pendiente_aprobacion"
    APROBADA_AUTOMATICAMENTE = "aprobada_automaticamente"


@dataclass(frozen=True, slots=True)
class Decision:
    estado: Outcome
    motivo: str
    aprueba: str | None
    datos: dict[str, FieldValue]


def evaluate(flow: Flow, raw: Mapping[str, object]) -> Decision:
    return apply_rules(flow, validate_data(flow, raw))


def validate_data(flow: Flow, raw: Mapping[str, object]) -> dict[str, FieldValue]:
    """Convierte y valida cada dato; reporta TODOS los problemas juntos."""
    problems: list[str] = []
    unknown = sorted(set(raw) - {f.campo for f in flow.datos_requeridos})
    if unknown:
        problems.append(f"campos desconocidos: {', '.join(unknown)}")

    datos: dict[str, FieldValue] = {}
    for field in flow.datos_requeridos:
        value = raw.get(field.campo)
        if value is None or (isinstance(value, str) and not value.strip()):
            problems.append(f"falta '{field.campo}'")
            continue
        try:
            datos[field.campo] = _convert(field, value)
        except ValueError as exc:
            problems.append(f"'{field.campo}' {exc}")

    if problems:
        raise InvalidInputError(f"Datos inválidos: {'; '.join(problems)}.")
    return datos


def apply_rules(flow: Flow, datos: Mapping[str, FieldValue]) -> Decision:
    """Rechazar gana siempre, sin importar el orden; luego, la primera aprobación que aplique."""
    triggered = [rule for rule in flow.reglas if _applies(rule, datos[rule.campo])]

    rejection = next((r for r in triggered if r.accion is RuleAction.RECHAZAR), None)
    if rejection is not None:
        return Decision(Outcome.RECHAZADA, rejection.mensaje or "", None, dict(datos))

    approval = next((r for r in triggered if r.accion is RuleAction.REQUIERE_APROBACION), None)
    if approval is not None:
        condition = _describe_condition(approval, datos[approval.campo])
        motivo = f"{condition}: requiere aprobación de {approval.aprueba}."
        return Decision(Outcome.PENDIENTE_APROBACION, motivo, approval.aprueba, dict(datos))

    return Decision(
        Outcome.APROBADA_AUTOMATICAMENTE, "Cumple todas las reglas del flujo.", None, dict(datos)
    )


def _convert(field: RequiredField, value: object) -> FieldValue:
    if field.tipo is FieldType.TEXTO:
        return _to_text(value)
    if field.tipo is FieldType.FECHA:
        return _to_date(value)
    number = _to_int(value) if field.tipo is FieldType.ENTERO else _to_decimal(value)
    if field.min is not None and number < field.min:
        raise ValueError(f"debe ser mayor o igual a {field.min}")
    if field.max is not None and number > field.max:
        raise ValueError(f"debe ser menor o igual a {field.max}")
    return number


def _to_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("debe ser texto")
    text = value.strip()
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"debe tener como máximo {MAX_TEXT_CHARS} caracteres")
    return text


def _to_int(value: object) -> int:
    # bool es subclase de int en Python: True no debe pasar como 1.
    if isinstance(value, bool):
        raise ValueError("debe ser un número entero")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and _INTEGER.match(value.strip()):
        return int(value.strip())
    raise ValueError("debe ser un número entero")


def _to_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | str | Decimal):
        raise ValueError("debe ser un número")
    try:
        # str() evita arrastrar el error binario de los float (0.1 -> 0.1000000000000000055...).
        number = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError("debe ser un número") from None
    if not number.is_finite():
        raise ValueError("debe ser un número")
    return number


def _to_date(value: object) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise ValueError("debe ser una fecha con formato AAAA-MM-DD")


def _applies(rule: Rule, value: FieldValue) -> bool:
    number = _as_number(value)
    if rule.mayor_que is not None:
        return number > rule.mayor_que
    return rule.menor_que is not None and number < rule.menor_que


def _describe_condition(rule: Rule, value: FieldValue) -> str:
    if rule.mayor_que is not None:
        return f"{rule.campo} ({value}) es mayor que {rule.mayor_que}"
    return f"{rule.campo} ({value}) es menor que {rule.menor_que}"


def _as_number(value: FieldValue) -> int | Decimal:
    # Las reglas solo usan campos numéricos (lo garantiza el modelo Flow al cargar).
    if isinstance(value, int | Decimal):
        return value
    raise TypeError(f"valor no numérico en una regla: {value!r}")
