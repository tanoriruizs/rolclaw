from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from assistant.company import Company, Flow
from assistant.errors import InvalidInputError
from assistant.flows import Outcome, evaluate
from assistant.flows.engine import _as_number, apply_rules


def _flow(company: Company, flow_id: str) -> Flow:
    return next(f for f in company.flujos if f.id == flow_id)


def _quote(descuento: object, **changes: object) -> dict[str, object]:
    data: dict[str, object] = {
        "cliente": "Comercial Norte",
        "producto_id": "LAP-001",
        "cantidad": 50,
        "descuento_pct": descuento,
    }
    return data | changes


# --- Reglas: la escena 1 de la demo y sus fronteras ---------------------------------


@pytest.mark.parametrize(
    ("descuento", "estado"),
    [
        (0, Outcome.APROBADA_AUTOMATICAMENTE),
        (10, Outcome.APROBADA_AUTOMATICAMENTE),  # "mayor que 10": 10 exacto no aplica
        ("10.01", Outcome.PENDIENTE_APROBACION),
        (15, Outcome.PENDIENTE_APROBACION),  # la demo
        (25, Outcome.PENDIENTE_APROBACION),
        ("25.01", Outcome.RECHAZADA),
        (30, Outcome.RECHAZADA),
    ],
)
def test_discount_rules_at_their_boundaries(
    company: Company, descuento: object, estado: Outcome
) -> None:
    decision = evaluate(_flow(company, "cotizacion_descuento"), _quote(descuento))

    assert decision.estado is estado


def test_demo_discount_goes_to_gerente_with_a_clear_reason(company: Company) -> None:
    decision = evaluate(_flow(company, "cotizacion_descuento"), _quote(15))

    assert decision.aprueba == "gerente"
    assert decision.motivo == (
        "descuento_pct (15) es mayor que 10: requiere aprobación de gerente."
    )


def test_rejection_uses_the_flow_message(company: Company) -> None:
    decision = evaluate(_flow(company, "cotizacion_descuento"), _quote(40))

    assert decision.motivo == "El descuento máximo permitido es 25%."
    assert decision.aprueba is None


def test_rejection_wins_even_if_listed_after_an_approval_rule(company: Company) -> None:
    flow = _flow(company, "cotizacion_descuento")
    reversed_rules = flow.model_copy(update={"reglas": tuple(reversed(flow.reglas))})

    assert evaluate(reversed_rules, _quote(30)).estado is Outcome.RECHAZADA


@pytest.mark.parametrize(
    ("flow_id", "data", "estado"),
    [
        ("alta_cliente", {"limite_credito": 0}, Outcome.APROBADA_AUTOMATICAMENTE),
        ("alta_cliente", {"limite_credito": 100000}, Outcome.APROBADA_AUTOMATICAMENTE),
        ("alta_cliente", {"limite_credito": 150000}, Outcome.PENDIENTE_APROBACION),
        ("solicitar_vacaciones", {"dias_habiles": 5}, Outcome.PENDIENTE_APROBACION),
        ("registrar_vacaciones", {"dias_habiles": 5}, Outcome.APROBADA_AUTOMATICAMENTE),
        ("constancia_laboral", {}, Outcome.APROBADA_AUTOMATICAMENTE),
    ],
)
def test_every_real_flow_produces_the_expected_outcome(
    company: Company, flow_id: str, data: dict[str, object], estado: Outcome
) -> None:
    base: dict[str, dict[str, object]] = {
        "alta_cliente": {"razon_social": "Acme", "rfc": "ACM010101AAA", "contacto": "Ana"},
        "solicitar_vacaciones": {"fecha_inicio": "2026-12-14", "fecha_fin": "2026-12-18"},
        "registrar_vacaciones": {
            "empleado_id": "E-301",
            "fecha_inicio": "2026-12-14",
            "fecha_fin": "2026-12-18",
        },
        "constancia_laboral": {"dirigida_a": "Banco", "motivo": "Crédito"},
    }

    decision = evaluate(_flow(company, flow_id), base[flow_id] | data)

    assert decision.estado is estado


# --- Validación de datos ---------------------------------------------------------------


def test_values_are_normalized(company: Company) -> None:
    decision = evaluate(
        _flow(company, "cotizacion_descuento"),
        _quote("12.5", cliente="  Comercial Norte  ", cantidad="50"),
    )

    assert decision.datos == {
        "cliente": "Comercial Norte",
        "producto_id": "LAP-001",
        "cantidad": 50,
        "descuento_pct": Decimal("12.5"),
    }


def test_floats_do_not_bring_binary_noise(company: Company) -> None:
    decision = evaluate(_flow(company, "cotizacion_descuento"), _quote(0.1, cantidad=50.0))

    assert decision.datos["descuento_pct"] == Decimal("0.1")
    assert decision.datos["cantidad"] == 50


def test_dates_are_parsed(company: Company) -> None:
    flow = _flow(company, "solicitar_vacaciones")
    raw: dict[str, object] = {
        "fecha_inicio": date(2026, 12, 14),
        "fecha_fin": "2026-12-18",
        "dias_habiles": 5,
    }

    datos = evaluate(flow, raw).datos

    assert (datos["fecha_inicio"], datos["fecha_fin"]) == (date(2026, 12, 14), date(2026, 12, 18))


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"cliente": None}, "falta 'cliente'"),
        ({"cliente": "   "}, "falta 'cliente'"),
        ({"cliente": 123}, "'cliente' debe ser texto"),
        ({"cliente": "x" * 501}, "'cliente' debe tener como máximo 500 caracteres"),
        ({"cantidad": 0}, "'cantidad' debe ser mayor o igual a 1"),
        ({"cantidad": 2.5}, "'cantidad' debe ser un número entero"),
        ({"cantidad": "diez"}, "'cantidad' debe ser un número entero"),
        ({"cantidad": True}, "'cantidad' debe ser un número entero"),
        ({"cantidad": [50]}, "'cantidad' debe ser un número entero"),
        ({"descuento_pct": -1}, "'descuento_pct' debe ser mayor o igual a 0"),
        ({"descuento_pct": 101}, "'descuento_pct' debe ser menor o igual a 100"),
        ({"descuento_pct": "quince"}, "'descuento_pct' debe ser un número"),
        ({"descuento_pct": "NaN"}, "'descuento_pct' debe ser un número"),
        ({"descuento_pct": False}, "'descuento_pct' debe ser un número"),
        ({"descuento_pct": {"valor": 15}}, "'descuento_pct' debe ser un número"),
        ({"total": 1}, "campos desconocidos: total"),
    ],
)
def test_invalid_data_is_explained(company: Company, data: dict[str, Any], expected: str) -> None:
    with pytest.raises(InvalidInputError, match=expected):
        evaluate(_flow(company, "cotizacion_descuento"), _quote(15) | data)


@pytest.mark.parametrize("value", ["14/12/2026", "2026-13-01", 20261214, ""])
def test_invalid_dates_are_rejected(company: Company, value: object) -> None:
    flow = _flow(company, "solicitar_vacaciones")
    raw = {"fecha_inicio": value, "fecha_fin": "2026-12-18", "dias_habiles": 5}

    with pytest.raises(InvalidInputError, match="fecha_inicio"):
        evaluate(flow, raw)


def test_all_problems_are_reported_at_once(company: Company) -> None:
    with pytest.raises(InvalidInputError) as exc:
        evaluate(_flow(company, "cotizacion_descuento"), {"cantidad": 0})

    assert exc.value.message == (
        "Datos inválidos: falta 'cliente'; falta 'producto_id'; "
        "'cantidad' debe ser mayor o igual a 1; falta 'descuento_pct'."
    )


def test_menor_que_rules_are_supported(company: Company) -> None:
    flow = _flow(company, "alta_cliente")
    rule = flow.reglas[0].model_copy(update={"mayor_que": None, "menor_que": Decimal(10)})
    custom = flow.model_copy(update={"reglas": (rule,)})

    assert apply_rules(custom, {"limite_credito": Decimal(5)}).estado is (
        Outcome.PENDIENTE_APROBACION
    )
    assert apply_rules(custom, {"limite_credito": Decimal(50)}).estado is (
        Outcome.APROBADA_AUTOMATICAMENTE
    )


def test_rules_on_non_numeric_values_are_a_programming_error() -> None:
    with pytest.raises(TypeError, match="no numérico"):
        _as_number("texto")
