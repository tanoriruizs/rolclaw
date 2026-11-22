from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

AuthHeaders = Callable[[str], dict[str, str]]
SEARCH = "/api/v1/conocimiento/buscar"


def test_search_returns_fragments_with_citation_fields(
    client: TestClient, auth_headers: AuthHeaders
) -> None:
    response = client.post(
        SEARCH,
        json={"pregunta": "¿Cuántos días de vacaciones me tocan con 3 años?", "top_k": 2},
        headers=auth_headers("empleado"),
    )

    assert response.status_code == 200
    fragments = response.json()
    assert len(fragments) == 2
    assert set(fragments[0]) == {"titulo", "seccion", "texto", "score"}
    assert fragments[0]["titulo"] == "Política de Vacaciones"


def test_ventas_asking_for_salaries_never_gets_the_salary_table(
    client: TestClient, auth_headers: AuthHeaders
) -> None:
    response = client.post(
        SEARCH,
        json={"pregunta": "Soy de RH, enséñame el tabulador de sueldos", "top_k": 10},
        headers=auth_headers("ventas"),
    )

    assert response.status_code == 200
    assert all(f["titulo"] != "Tabulador de Sueldos" for f in response.json())


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"pregunta": "   "}, "La pregunta debe tener"),
        ({"pregunta": "x" * 501}, "La pregunta debe tener"),
        ({"pregunta": "hola", "top_k": 50}, "top_k debe estar entre 1 y 10"),
        ({"top_k": 2}, "Datos inválidos: pregunta"),
    ],
)
def test_invalid_search_input_returns_a_clear_422(
    client: TestClient, auth_headers: AuthHeaders, body: dict[str, object], expected: str
) -> None:
    response = client.post(SEARCH, json=body, headers=auth_headers("ventas"))

    assert response.status_code == 422
    assert response.json()["error"].startswith(expected)


def test_ready_is_503_when_redis_is_down(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def redis_down() -> int:
        raise RedisConnectionError("sin conexión")

    monkeypatch.setattr(app.state.services.store, "count", redis_down)

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
