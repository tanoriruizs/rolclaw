import pytest

from assistant.company import Rule
from assistant.flows.catalog import describe_rule


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        (
            {"campo": "monto", "menor_que": 5, "accion": "rechazar", "mensaje": "Muy poco."},
            "Si monto es menor que 5, se rechaza: Muy poco.",
        ),
        (
            {
                "campo": "monto",
                "mayor_que": 100000,
                "accion": "requiere_aprobacion",
                "aprueba": "gerente",
            },
            "Si monto es mayor que 100000, requiere aprobación de gerente.",
        ),
    ],
)
def test_describe_rule(rule: dict[str, object], expected: str) -> None:
    assert describe_rule(Rule.model_validate(rule)) == expected
