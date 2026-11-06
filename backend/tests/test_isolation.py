from uuid import uuid4

import pytest

from tests.test_orders import ORDER, create_order

ALICE = "alice@example.com"
BOB = "bob@example.com"
WRITES = [("GET", None), ("PATCH", {"provider_name": "Intruder"}), ("DELETE", None)]


@pytest.mark.parametrize(("method", "body"), WRITES)
def test_other_user_gets_404_on_get_patch_delete(client, auth_headers, method, body):
    alice, bob = auth_headers(ALICE), auth_headers(BOB)
    order = create_order(client, alice)
    path = f"/v1/orders/{order['id']}"

    assert client.request(method, path, json=body, headers=bob).status_code == 404

    assert client.get(path, headers=alice).json() == order


def test_other_users_orders_absent_from_list(client, auth_headers):
    alice, bob = auth_headers(ALICE), auth_headers(BOB)
    create_order(client, alice)
    mine = create_order(client, bob)

    page = client.get("/v1/orders", headers=bob).json()

    assert [order["id"] for order in page["items"]] == [mine["id"]]
    assert page["total"] == 1


@pytest.mark.parametrize(("method", "body"), WRITES)
def test_missing_and_foreign_ids_return_identical_bodies(client, auth_headers, method, body):
    alice, bob = auth_headers(ALICE), auth_headers(BOB)
    foreign = create_order(client, alice)["id"]

    foreign_response = client.request(method, f"/v1/orders/{foreign}", json=body, headers=bob)
    missing_response = client.request(method, f"/v1/orders/{uuid4()}", json=body, headers=bob)

    assert foreign_response.status_code == missing_response.status_code == 404
    assert foreign_response.content == missing_response.content


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/orders"),
        ("POST", "/v1/orders"),
        ("GET", "/v1/orders/{id}"),
        ("PATCH", "/v1/orders/{id}"),
        ("DELETE", "/v1/orders/{id}"),
    ],
)
def test_unauthenticated_order_routes_return_401(client, method, path):
    url = path.format(id=uuid4())
    body = ORDER if method == "POST" else {} if method == "PATCH" else None
    assert client.request(method, url, json=body).status_code == 401
