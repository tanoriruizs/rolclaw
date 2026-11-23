from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

AuthHeaders = Callable[[str], dict[str, str]]


def test_demo_laptop_price(client: TestClient, auth_headers: AuthHeaders) -> None:
    response = client.get(
        "/api/v1/catalogo", params={"busqueda": "laptops"}, headers=auth_headers("ventas")
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "LAP-001",
            "nombre": "Laptop Cardinal Pro 14",
            "categoria": "laptops",
            "precio": "15000",
            "moneda": "MXN",
        }
    ]


@pytest.mark.parametrize(
    ("busqueda", "expected"),
    [
        (None, 7),
        ("monitor", 2),
        ("MONITORES 27", 1),
        ("ratón y teclado", 0),
        ("inalámbricos", 1),
        ("SRV-001", 1),
    ],
)
def test_search_is_case_and_accent_insensitive(
    client: TestClient, auth_headers: AuthHeaders, busqueda: str | None, expected: int
) -> None:
    params = {"busqueda": busqueda} if busqueda else {}

    items = client.get("/api/v1/catalogo", params=params, headers=auth_headers("gerente")).json()

    assert len(items) == expected


@pytest.mark.parametrize("role", ["rh", "empleado"])
def test_roles_without_permission_cannot_see_prices(
    client: TestClient, auth_headers: AuthHeaders, role: str
) -> None:
    response = client.get("/api/v1/catalogo", headers=auth_headers(role))

    assert response.status_code == 403


def test_long_queries_are_rejected(client: TestClient, auth_headers: AuthHeaders) -> None:
    response = client.get(
        "/api/v1/catalogo", params={"busqueda": "x" * 101}, headers=auth_headers("ventas")
    )

    assert response.status_code == 422
