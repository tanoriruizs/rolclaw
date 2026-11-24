from typing import Any

import pytest
from pydantic import ValidationError

from assistant.company import Flow, Rule
from assistant.company.models import Calculation, Calendar, Catalog, RequiredField


def _flow(**changes: Any) -> dict[str, Any]:
    flow: dict[str, Any] = {
        "id": "prueba",
        "nombre": "Prueba",
        "roles": ["ventas"],
        "descripcion": "Flujo de prueba",
        "datos_requeridos": [
            {"campo": "cliente", "tipo": "texto"},
            {"campo": "monto", "tipo": "decimal", "min": 0},
        ],
        "pasos": ["Pedir datos"],
        "reglas": [],
    }
    return flow | changes


def test_valid_flow() -> None:
    rule = {
        "campo": "monto",
        "mayor_que": 10,
        "accion": "requiere_aprobacion",
        "aprueba": "gerente",
    }

    flow = Flow.model_validate(_flow(reglas=[rule]))

    assert flow.reglas[0].aprueba == "gerente"


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ({"campo": "monto", "accion": "rechazar", "mensaje": "x"}, "exactamente una"),
        (
            {
                "campo": "monto",
                "mayor_que": 1,
                "menor_que": 5,
                "accion": "rechazar",
                "mensaje": "x",
            },
            "exactamente una",
        ),
        ({"campo": "monto", "mayor_que": 1, "accion": "requiere_aprobacion"}, "necesita 'aprueba'"),
        ({"campo": "monto", "mayor_que": 1, "accion": "rechazar"}, "necesita 'mensaje'"),
        (
            {
                "campo": "monto",
                "mayor_que": 1,
                "accion": "rechazar",
                "mensaje": "x",
                "aprueba": "g",
            },
            "solo aplica",
        ),
        ({"campo": "monto", "mayor_que": 1, "accion": "borrar"}, "accion"),
    ],
)
def test_invalid_rules(rule: dict[str, Any], expected: str) -> None:
    with pytest.raises(ValidationError, match=expected):
        Rule.model_validate(rule)


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ({"campo": "cliente", "tipo": "texto", "min": 1}, "solo aplican a campos numéricos"),
        ({"campo": "monto", "tipo": "entero", "min": 5, "max": 1}, "min no puede ser mayor"),
        ({"campo": "Monto", "tipo": "entero"}, "campo"),
        ({"campo": "monto", "tipo": "moneda"}, "tipo"),
    ],
)
def test_invalid_required_fields(field: dict[str, Any], expected: str) -> None:
    with pytest.raises(ValidationError, match=expected):
        RequiredField.model_validate(field)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        (
            {"reglas": [{"campo": "otro", "mayor_que": 1, "accion": "rechazar", "mensaje": "x"}]},
            "que no es un campo del flujo",
        ),
        (
            {
                "reglas": [
                    {"campo": "cliente", "mayor_que": 1, "accion": "rechazar", "mensaje": "x"}
                ]
            },
            "necesita un campo numérico",
        ),
        (
            {
                "datos_requeridos": [
                    {"campo": "a", "tipo": "texto"},
                    {"campo": "a", "tipo": "texto"},
                ]
            },
            "campo duplicado",
        ),
        ({"pasos": []}, "pasos"),
        ({"extra": True}, "extra"),
    ],
)
def test_invalid_flows(changes: dict[str, Any], expected: str) -> None:
    with pytest.raises(ValidationError, match=expected):
        Flow.model_validate(_flow(**changes))


def test_duplicate_products_are_rejected() -> None:
    product = {"id": "A", "nombre": "A", "categoria": "c", "precio": 1}

    with pytest.raises(ValidationError, match="producto duplicado"):
        Catalog.model_validate({"moneda": "MXN", "productos": [product, product]})


# --- Campos calculados por el servidor -------------------------------------------------

_QUOTE_FIELDS = [
    {"campo": "producto_id", "tipo": "producto"},
    {"campo": "cantidad", "tipo": "entero"},
    {"campo": "descuento_pct", "tipo": "decimal"},
    {"campo": "cliente", "tipo": "texto"},
]
_CON = {"producto": "producto_id", "cantidad": "cantidad", "descuento_pct": "descuento_pct"}
_TOTAL: dict[str, Any] = {"campo": "total", "calculo": "total_con_descuento", "con": _CON}


def test_rules_can_use_calculated_fields() -> None:
    rule = {"campo": "total", "mayor_que": 100000, "accion": "rechazar", "mensaje": "Mucho."}

    flow = Flow.model_validate(
        _flow(datos_requeridos=_QUOTE_FIELDS, calculados=[_TOTAL], reglas=[rule])
    )

    assert flow.calculation(Calculation.TOTAL_CON_DESCUENTO) is flow.calculados[0]
    assert flow.calculation(Calculation.DIAS_HABILES) is None


@pytest.mark.parametrize(
    ("calculados", "expected"),
    [
        ([_TOTAL | {"con": {"producto": "producto_id"}}], "necesita 'con'"),
        ([_TOTAL | {"con": _CON | {"extra": "cliente"}}], "necesita 'con'"),
        ([_TOTAL | {"calculo": "promedio"}], "calculo"),
        ([_TOTAL | {"campo": "cantidad"}], "campo duplicado"),
        ([_TOTAL, _TOTAL], "campo duplicado"),
        (
            [_TOTAL | {"con": _CON | {"cantidad": "no_existe"}}],
            "usa 'no_existe', que no está definido antes",
        ),
        (
            [_TOTAL | {"con": _CON | {"cantidad": "cliente"}}],
            "'cliente' no sirve como 'cantidad'",
        ),
        (
            [_TOTAL | {"con": _CON | {"producto": "cliente"}}],
            "'cliente' no sirve como 'producto'",
        ),
        (
            [
                {
                    "campo": "restantes",
                    "calculo": "saldo_vacaciones",
                    "con": {"inicio": "cliente", "dias": "cantidad"},
                }
            ],
            "'cliente' no sirve como 'inicio'",
        ),
    ],
)
def test_invalid_calculated_fields(calculados: list[dict[str, Any]], expected: str) -> None:
    with pytest.raises(ValidationError, match=expected):
        Flow.model_validate(_flow(datos_requeridos=_QUOTE_FIELDS, calculados=calculados))


def test_a_calculation_cannot_use_a_field_calculated_later() -> None:
    days = {
        "campo": "dias",
        "calculo": "dias_habiles",
        "con": {"inicio": "inicio", "fin": "fin"},
    }
    balance = {
        "campo": "restantes",
        "calculo": "saldo_vacaciones",
        "con": {"inicio": "inicio", "dias": "dias"},
    }
    fields = [{"campo": "inicio", "tipo": "fecha"}, {"campo": "fin", "tipo": "fecha"}]

    Flow.model_validate(_flow(datos_requeridos=fields, calculados=[days, balance]))
    with pytest.raises(ValidationError, match="usa 'dias', que no está definido antes"):
        Flow.model_validate(_flow(datos_requeridos=fields, calculados=[balance, days]))


def test_rules_cannot_use_non_numeric_calculated_inputs() -> None:
    rule = {"campo": "producto_id", "mayor_que": 1, "accion": "rechazar", "mensaje": "x"}

    with pytest.raises(ValidationError, match="necesita un campo numérico"):
        Flow.model_validate(_flow(datos_requeridos=_QUOTE_FIELDS, reglas=[rule]))


# --- Calendario -------------------------------------------------------------------------


def _calendar(tiers: list[tuple[int, int]]) -> Calendar:
    return Calendar.model_validate(
        {"vacaciones_por_antiguedad": [{"desde": d, "dias": n} for d, n in tiers]}
    )


@pytest.mark.parametrize(
    ("years", "days"),
    [(-1, 0), (0, 0), (1, 12), (2, 14), (5, 20), (6, 22), (10, 22), (11, 24), (40, 24)],
)
def test_vacation_days_by_years_of_service(years: int, days: int) -> None:
    calendar = _calendar([(1, 12), (2, 14), (5, 20), (6, 22), (11, 24)])

    assert calendar.vacation_days(years) == days


@pytest.mark.parametrize("tiers", [[(2, 14), (1, 12)], [(1, 12), (1, 14)], []])
def test_vacation_table_must_be_ordered_and_not_empty(tiers: list[tuple[int, int]]) -> None:
    with pytest.raises(ValidationError):
        _calendar(tiers)
