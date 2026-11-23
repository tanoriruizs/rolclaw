from collections.abc import Iterator

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from fastapi.testclient import TestClient
from pydantic import SecretStr

from servicenow_mock.main import MockSettings, create_app, filter_records

SECRET = "secreto-de-prueba-del-mock"
TABLE_URL = "/api/now/table/sc_request"


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = MockSettings(servicenow_client_secret=SecretStr(SECRET))
    app = create_app(settings, redis=FakeAsyncRedis(server=FakeServer()))
    with TestClient(app) as test_client:
        yield test_client


def _token(client: TestClient, secret: str = SECRET) -> dict[str, str]:
    response = client.post(
        "/oauth_token.do",
        data={
            "grant_type": "client_credentials",
            "client_id": "cardinal-assistant",
            "client_secret": secret,
        },
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_oauth_client_credentials_returns_a_bearer_token(client: TestClient) -> None:
    response = client.post(
        "/oauth_token.do",
        data={
            "grant_type": "client_credentials",
            "client_id": "cardinal-assistant",
            "client_secret": SECRET,
        },
    )

    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 1800
    assert len(body["access_token"]) >= 40


@pytest.mark.parametrize(
    ("form", "status", "error"),
    [
        ({"grant_type": "password"}, 400, "unsupported_grant_type"),
        (
            {"grant_type": "client_credentials", "client_id": "x", "client_secret": SECRET},
            401,
            "invalid_client",
        ),
        (
            {"grant_type": "client_credentials", "client_id": "cardinal-assistant"},
            401,
            "invalid_client",
        ),
    ],
)
def test_oauth_rejects_bad_requests(
    client: TestClient, form: dict[str, str], status: int, error: str
) -> None:
    response = client.post("/oauth_token.do", data=form)

    assert response.status_code == status
    assert response.json() == {"error": error}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer inventado"}])
def test_table_api_requires_a_valid_token(client: TestClient, headers: dict[str, str]) -> None:
    response = client.get(TABLE_URL, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "User Not Authenticated"


def test_create_assigns_number_and_system_fields(client: TestClient) -> None:
    headers = _token(client)

    first = client.post(
        TABLE_URL, json={"short_description": "Uno", "number": "X"}, headers=headers
    )
    second = client.post(
        TABLE_URL, json={"short_description": "Dos", "u_total": 637500}, headers=headers
    )

    assert first.status_code == 201
    record = first.json()["result"]
    assert record["number"] == "REQ0010001"
    assert len(record["sys_id"]) == 32
    assert record["sys_created_on"] == record["sys_updated_on"]
    assert second.json()["result"]["number"] == "REQ0010002"
    assert second.json()["result"]["u_total"] == "637500"  # todo se guarda como texto


def test_get_update_and_not_found(client: TestClient) -> None:
    headers = _token(client)
    sys_id = client.post(TABLE_URL, json={"approval": "requested"}, headers=headers).json()[
        "result"
    ]["sys_id"]

    updated = client.patch(
        f"{TABLE_URL}/{sys_id}", json={"approval": "approved", "sys_id": "x"}, headers=headers
    )

    assert updated.json()["result"]["approval"] == "approved"
    assert updated.json()["result"]["sys_id"] == sys_id
    assert client.get(f"{TABLE_URL}/{sys_id}", headers=headers).json()["result"]["approval"] == (
        "approved"
    )
    assert client.get(f"{TABLE_URL}/no-existe", headers=headers).status_code == 404
    assert client.patch(f"{TABLE_URL}/no-existe", json={}, headers=headers).status_code == 404


def test_query_filters_orders_and_limits(client: TestClient) -> None:
    headers = _token(client)
    for rol, estado in [("ventas", "requested"), ("rh", "requested"), ("ventas", "approved")]:
        client.post(TABLE_URL, json={"u_rol": rol, "approval": estado}, headers=headers)

    response = client.get(
        TABLE_URL,
        params={"sysparm_query": "u_rol=ventas^ORDERBYDESCnumber", "sysparm_limit": "5"},
        headers=headers,
    )
    numbers = [r["number"] for r in response.json()["result"]]

    assert numbers == ["REQ0010003", "REQ0010001"]
    limited = client.get(TABLE_URL, params={"sysparm_limit": "1"}, headers=headers)
    assert len(limited.json()["result"]) == 1


@pytest.mark.parametrize("body", ["no es json", "[1, 2]", '{"datos": {"a": 1}}', '{"lista": [1]}'])
def test_invalid_bodies_are_rejected(client: TestClient, body: str) -> None:
    response = client.post(
        TABLE_URL, content=body, headers=_token(client) | {"Content-Type": "application/json"}
    )

    assert response.status_code == 400


def test_patch_with_invalid_body_is_rejected(client: TestClient) -> None:
    headers = _token(client)
    sys_id = client.post(TABLE_URL, json={}, headers=headers).json()["result"]["sys_id"]

    response = client.patch(f"{TABLE_URL}/{sys_id}", json={"datos": {"a": 1}}, headers=headers)

    assert response.status_code == 400


def test_invalid_query_returns_400(client: TestClient) -> None:
    response = client.get(
        TABLE_URL, params={"sysparm_query": "u_rolLIKEventas"}, headers=_token(client)
    )

    assert response.status_code == 400


def test_filter_records_supports_not_equal_and_ascending_order() -> None:
    records = [{"n": "2", "s": "a"}, {"n": "1", "s": "b"}, {"n": "3", "s": "a"}]

    assert filter_records(records, "s!=b^ORDERBYn") == [{"n": "2", "s": "a"}, {"n": "3", "s": "a"}]
    assert filter_records(records, "") == records


def test_null_values_become_empty_strings(client: TestClient) -> None:
    headers = _token(client)

    record = client.post(TABLE_URL, json={"comentario": None}, headers=headers).json()["result"]

    assert record["comentario"] == ""
