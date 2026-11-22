from typing import Any

import pytest
from pydantic import ValidationError

from assistant.company import Flow, Rule
from assistant.company.models import Catalog, RequiredField


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
            "no está en datos_requeridos",
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
