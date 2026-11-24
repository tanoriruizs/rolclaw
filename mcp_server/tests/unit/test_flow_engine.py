from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from assistant.company import Company, Flow
from assistant.errors import InvalidInputError
from assistant.flows import FlowContext, Outcome, evaluate, service_year
from assistant.flows.calculations import as_date, as_number
from assistant.flows.engine import _as_number, apply_rules


@pytest.fixture
def context(company: Company) -> FlowContext:
    # Juan Méndez (E-301) ingresó el 2023-08-14.
    return replace(FlowContext.from_company(company), fecha_ingreso=date(2023, 8, 14))


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
    company: Company, context: FlowContext, descuento: object, estado: Outcome
) -> None:
    decision = evaluate(_flow(company, "cotizacion_descuento"), _quote(descuento), context)

    assert decision.estado is estado


def test_demo_discount_goes_to_gerente_with_a_clear_reason(
    company: Company, context: FlowContext
) -> None:
    decision = evaluate(_flow(company, "cotizacion_descuento"), _quote(15), context)

    assert decision.aprueba == "gerente"
    assert decision.motivo == (
        "descuento_pct (15) es mayor que 10: requiere aprobación de gerente."
    )


def test_rejection_uses_the_flow_message(company: Company, context: FlowContext) -> None:
    decision = evaluate(_flow(company, "cotizacion_descuento"), _quote(40), context)

    assert decision.motivo == "El descuento máximo permitido es 25%."
    assert decision.aprueba is None


def test_rejection_wins_even_if_listed_after_an_approval_rule(
    company: Company, context: FlowContext
) -> None:
    flow = _flow(company, "cotizacion_descuento")
    reversed_rules = flow.model_copy(update={"reglas": tuple(reversed(flow.reglas))})

    assert evaluate(reversed_rules, _quote(30), context).estado is Outcome.RECHAZADA


# --- El servidor calcula: precio y total ----------------------------------------------


def test_server_computes_the_demo_total_from_the_catalog(
    company: Company, context: FlowContext
) -> None:
    decision = evaluate(_flow(company, "cotizacion_descuento"), _quote(15), context)

    assert decision.datos["precio_unitario"] == Decimal(15000)
    assert decision.datos["total"] == Decimal("637500.00")


@pytest.mark.parametrize(
    ("producto", "cantidad", "descuento", "total"),
    [
        ("LAP-001", 1, 0, "15000.00"),
        ("lap-001", 3, "12.5", "39375.00"),  # el id no distingue mayúsculas
        ("KIT-001", 7, "33.333", "3033.35"),  # 650 * 7 * 0.66667 = 3033.3485 -> redondea
        ("MON-002", 1, 100, "0.00"),
    ],
)
def test_totals_are_exact_decimals(
    company: Company,
    context: FlowContext,
    producto: str,
    cantidad: int,
    descuento: object,
    total: str,
) -> None:
    data = _quote(descuento, producto_id=producto, cantidad=cantidad)

    decision = evaluate(_flow(company, "cotizacion_descuento"), data, context)

    assert decision.datos["total"] == Decimal(total)
    assert decision.datos["producto_id"] == producto.upper()


@pytest.mark.parametrize("producto", ["LAP-999", "laptop", "LAP-001 OR 1=1"])
def test_unknown_products_are_rejected(
    company: Company, context: FlowContext, producto: str
) -> None:
    with pytest.raises(InvalidInputError, match="'producto_id' no existe en el catálogo"):
        evaluate(_flow(company, "cotizacion_descuento"), _quote(15, producto_id=producto), context)


@pytest.mark.parametrize("field", ["total", "precio_unitario"])
def test_the_agent_cannot_send_calculated_fields(
    company: Company, context: FlowContext, field: str
) -> None:
    with pytest.raises(InvalidInputError, match=f"no envíes {field}: lo calcula el servidor"):
        evaluate(_flow(company, "cotizacion_descuento"), _quote(15) | {field: 1}, context)


# --- El servidor calcula: días hábiles y saldo de vacaciones ---------------------------


def _vacation(company: Company, context: FlowContext, start: str, end: str) -> Any:
    data = {"fecha_inicio": start, "fecha_fin": end}
    return evaluate(_flow(company, "solicitar_vacaciones"), data, context)


@pytest.mark.parametrize(
    ("start", "end", "dias"),
    [
        ("2026-12-14", "2026-12-18", 5),  # la demo: lunes a viernes
        ("2026-12-14", "2026-12-14", 1),  # un solo día
        ("2026-12-11", "2026-12-14", 2),  # viernes a lunes: el fin de semana no cuenta
        ("2026-12-22", "2026-12-26", 3),  # el 25 es festivo y el 26 es sábado
        ("2026-12-28", "2027-01-01", 4),  # cruza de año; el 1 de enero es festivo
        ("2026-11-16", "2026-11-20", 4),  # el lunes 16 de noviembre es festivo
    ],
)
def test_business_days_skip_weekends_and_holidays(
    company: Company, context: FlowContext, start: str, end: str, dias: int
) -> None:
    decision = _vacation(company, context, start, end)

    assert decision.datos["dias_habiles"] == dias
    assert decision.estado is Outcome.PENDIENTE_APROBACION


def test_demo_vacation_has_16_days_by_seniority(company: Company, context: FlowContext) -> None:
    decision = _vacation(company, context, "2026-12-14", "2026-12-18")

    assert decision.datos["dias_por_antiguedad"] == 16
    assert decision.datos["dias_restantes"] == 11
    assert decision.aprueba == "gerente"


@pytest.mark.parametrize(
    ("start", "end"),
    [("2026-12-19", "2026-12-20"), ("2026-12-25", "2026-12-25"), ("2026-12-25", "2026-12-27")],
)
def test_periods_without_business_days_are_rejected(
    company: Company, context: FlowContext, start: str, end: str
) -> None:
    decision = _vacation(company, context, start, end)

    assert decision.estado is Outcome.RECHAZADA
    assert decision.motivo == "El periodo no incluye días hábiles."


@pytest.mark.parametrize(
    ("used", "estado", "restantes"),
    [
        (0, Outcome.PENDIENTE_APROBACION, 11),
        (11, Outcome.PENDIENTE_APROBACION, 0),  # justo alcanzan
        (12, Outcome.RECHAZADA, -1),  # falta un día
        (16, Outcome.RECHAZADA, -5),
    ],
)
def test_vacation_balance_boundaries(
    company: Company, context: FlowContext, used: int, estado: Outcome, restantes: int
) -> None:
    with_used = replace(context, dias_vacaciones_usados=used)

    decision = _vacation(company, with_used, "2026-12-14", "2026-12-18")

    assert decision.estado is estado
    assert decision.datos["dias_restantes"] == restantes


@pytest.mark.parametrize(
    ("hired", "start", "dias"),
    [
        (date(2026, 3, 3), "2026-12-14", 0),  # menos de un año: todavía no le tocan
        (date(2025, 12, 14), "2026-12-14", 12),  # cumple un año ese mismo día
        (date(2025, 12, 15), "2026-12-14", 0),  # le falta un día para el año
        (date(2023, 8, 14), "2026-08-13", 14),  # un día antes de cumplir 3 años
        (date(2023, 8, 14), "2026-08-14", 16),  # el día que cumple 3 años
        (date(2015, 2, 2), "2026-12-14", 24),  # 11 años
        (date(2024, 2, 29), "2027-03-01", 16),  # ingresó un 29 de febrero
    ],
)
def test_days_by_seniority_boundaries(
    company: Company, context: FlowContext, hired: date, start: str, dias: int
) -> None:
    decision = _vacation(company, replace(context, fecha_ingreso=hired), start, start)

    assert decision.datos["dias_por_antiguedad"] == dias


def test_someone_without_a_full_year_cannot_take_vacations(
    company: Company, context: FlowContext
) -> None:
    new_hire = replace(context, fecha_ingreso=date(2026, 3, 3))

    decision = _vacation(company, new_hire, "2026-12-14", "2026-12-18")

    assert decision.estado is Outcome.RECHAZADA
    assert decision.motivo == "No tienes días de vacaciones suficientes para ese periodo."


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2026-12-18", "2026-12-14", "la fecha final es anterior a la inicial"),
        ("2026-01-05", "2027-06-01", "el periodo no puede ser mayor a un año"),
    ],
)
def test_invalid_periods_are_explained(
    company: Company, context: FlowContext, start: str, end: str, expected: str
) -> None:
    with pytest.raises(InvalidInputError, match=expected):
        _vacation(company, context, start, end)


def test_balance_needs_the_hire_date(company: Company, context: FlowContext) -> None:
    with pytest.raises(InvalidInputError, match="fecha de ingreso"):
        _vacation(company, replace(context, fecha_ingreso=None), "2026-12-14", "2026-12-18")


def test_the_agent_cannot_send_its_own_business_days(
    company: Company, context: FlowContext
) -> None:
    data = {"fecha_inicio": "2026-12-22", "fecha_fin": "2026-12-26", "dias_habiles": 1}

    with pytest.raises(InvalidInputError, match="no envíes dias_habiles"):
        evaluate(_flow(company, "solicitar_vacaciones"), data, context)


@pytest.mark.parametrize(
    ("empleado", "ok"), [("E-301", True), ("e-301", True), ("E-999", False), ("Juan", False)]
)
def test_rh_can_only_register_vacations_for_real_employees(
    company: Company, context: FlowContext, empleado: str, ok: bool
) -> None:
    flow = _flow(company, "registrar_vacaciones")
    data = {"empleado_id": empleado, "fecha_inicio": "2026-12-14", "fecha_fin": "2026-12-18"}

    if ok:
        decision = evaluate(flow, data, context)
        assert decision.datos == {
            "empleado_id": "E-301",
            "fecha_inicio": date(2026, 12, 14),
            "fecha_fin": date(2026, 12, 18),
            "dias_habiles": 5,
        }
        assert decision.estado is Outcome.APROBADA_AUTOMATICAMENTE
    else:
        with pytest.raises(InvalidInputError, match="no es un número de empleado válido"):
            evaluate(flow, data, context)


# --- Otros flujos y validación de datos ------------------------------------------------


@pytest.mark.parametrize(
    ("flow_id", "data", "estado"),
    [
        ("alta_cliente", {"limite_credito": 0}, Outcome.APROBADA_AUTOMATICAMENTE),
        ("alta_cliente", {"limite_credito": 100000}, Outcome.APROBADA_AUTOMATICAMENTE),
        ("alta_cliente", {"limite_credito": 150000}, Outcome.PENDIENTE_APROBACION),
        ("constancia_laboral", {}, Outcome.APROBADA_AUTOMATICAMENTE),
    ],
)
def test_flows_without_calculations_keep_working(
    company: Company,
    context: FlowContext,
    flow_id: str,
    data: dict[str, object],
    estado: Outcome,
) -> None:
    base: dict[str, dict[str, object]] = {
        "alta_cliente": {"razon_social": "Acme", "rfc": "ACM010101AAA", "contacto": "Ana"},
        "constancia_laboral": {"dirigida_a": "Banco", "motivo": "Crédito"},
    }

    decision = evaluate(_flow(company, flow_id), base[flow_id] | data, context)

    assert decision.estado is estado


def test_values_are_normalized(company: Company, context: FlowContext) -> None:
    decision = evaluate(
        _flow(company, "cotizacion_descuento"),
        _quote("12.5", cliente="  Comercial Norte  ", cantidad="50"),
        context,
    )

    assert decision.datos["cliente"] == "Comercial Norte"
    assert decision.datos["cantidad"] == 50
    assert decision.datos["descuento_pct"] == Decimal("12.5")


def test_floats_do_not_bring_binary_noise(company: Company, context: FlowContext) -> None:
    decision = evaluate(_flow(company, "cotizacion_descuento"), _quote(0.1, cantidad=50.0), context)

    assert decision.datos["descuento_pct"] == Decimal("0.1")
    assert decision.datos["total"] == Decimal("749250.00")


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"cliente": None}, "falta 'cliente'"),
        ({"cliente": "   "}, "falta 'cliente'"),
        ({"cliente": 123}, "'cliente' debe ser texto"),
        ({"cliente": "x" * 501}, "'cliente' debe tener como máximo 500 caracteres"),
        ({"producto_id": 15}, "'producto_id' debe ser texto"),
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
        ({"comision": 1}, "campos desconocidos: comision"),
    ],
)
def test_invalid_data_is_explained(
    company: Company, context: FlowContext, data: dict[str, Any], expected: str
) -> None:
    with pytest.raises(InvalidInputError, match=expected):
        evaluate(_flow(company, "cotizacion_descuento"), _quote(15) | data, context)


@pytest.mark.parametrize("value", ["14/12/2026", "2026-13-01", 20261214, ""])
def test_invalid_dates_are_rejected(company: Company, context: FlowContext, value: object) -> None:
    raw = {"fecha_inicio": value, "fecha_fin": "2026-12-18"}

    with pytest.raises(InvalidInputError, match="fecha_inicio"):
        evaluate(_flow(company, "solicitar_vacaciones"), raw, context)


def test_all_problems_are_reported_at_once(company: Company, context: FlowContext) -> None:
    with pytest.raises(InvalidInputError) as exc:
        evaluate(_flow(company, "cotizacion_descuento"), {"cantidad": 0, "total": 5}, context)

    assert exc.value.message == (
        "Datos inválidos: no envíes total: lo calcula el servidor; falta 'cliente'; "
        "falta 'producto_id'; 'cantidad' debe ser mayor o igual a 1; falta 'descuento_pct'."
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


def test_dates_can_arrive_as_date_objects(company: Company, context: FlowContext) -> None:
    raw: dict[str, object] = {"fecha_inicio": date(2026, 12, 14), "fecha_fin": "2026-12-18"}

    decision = evaluate(_flow(company, "solicitar_vacaciones"), raw, context)

    assert decision.datos["fecha_inicio"] == date(2026, 12, 14)
    assert decision.datos["dias_habiles"] == 5


@pytest.mark.parametrize(
    ("convert", "value"),
    [(as_number, "15"), (as_number, True), (as_number, date(2026, 1, 1)), (as_date, 5)],
)
def test_calculations_reject_values_of_the_wrong_type(convert: Any, value: Any) -> None:
    with pytest.raises(TypeError):
        convert(value)


@pytest.mark.parametrize(
    ("hired", "on", "expected"),
    [
        (date(2023, 8, 14), date(2026, 12, 14), (date(2026, 8, 14), date(2027, 8, 14))),
        (date(2023, 8, 14), date(2026, 8, 13), (date(2025, 8, 14), date(2026, 8, 14))),
        (date(2023, 8, 14), date(2026, 8, 14), (date(2026, 8, 14), date(2027, 8, 14))),
        (date(2024, 2, 29), date(2025, 6, 1), (date(2025, 2, 28), date(2026, 2, 28))),
        (date(2026, 3, 3), date(2026, 5, 1), (date(2026, 3, 3), date(2027, 3, 3))),
    ],
)
def test_service_year_goes_from_anniversary_to_anniversary(
    hired: date, on: date, expected: tuple[date, date]
) -> None:
    assert service_year(hired, on) == expected
