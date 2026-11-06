from uuid import uuid4

import pytest

from app.models import Document, FieldEvidence, Order, ReviewFlag
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
def test_missing_foreign_and_deleted_ids_look_identical(client, auth_headers, method, body):
    alice, bob = auth_headers(ALICE), auth_headers(BOB)
    foreign = create_order(client, alice)["id"]
    deleted = create_order(client, bob)["id"]
    assert client.delete(f"/v1/orders/{deleted}", headers=bob).status_code == 204

    responses = [
        client.request(method, f"/v1/orders/{order_id}", json=body, headers=bob)
        for order_id in (foreign, deleted, uuid4())
    ]

    assert [response.status_code for response in responses] == [404, 404, 404]
    assert len({response.content for response in responses}) == 1
    # Names, not values: X-Request-ID differs by design, but an extra header would be an oracle.
    assert len({tuple(sorted(response.headers.keys())) for response in responses}) == 1


def test_rows_owned_by_another_user_never_appear_through_an_order(client, db, auth_headers):
    alice, bob = auth_headers(ALICE), auth_headers(BOB)
    order = create_order(client, alice)
    path = f"/v1/orders/{order['id']}"
    bob_id = client.get("/auth/me", headers=bob).json()["id"]
    document = Document(
        owner_id=bob_id,
        sha256="0" * 64,
        byte_size=8,
        page_count=1,
        text_pages=[1],
        content=b"%PDF-bob",
    )
    db.add(document)
    db.flush()
    document_id = document.id
    row = db.get(Order, order["id"])
    row.document_id = document_id  # no route can do this: only a bug or a bad migration could
    db.add(row)
    db.add(
        FieldEvidence(
            owner_id=bob_id,
            order_id=order["id"],
            field="provider_npi",
            source_quote="quote from another user",
            verification="verified",
        )
    )
    db.add(ReviewFlag(owner_id=bob_id, order_id=order["id"], code="missing"))
    db.commit()

    single = client.get(path, headers=alice)
    listed = client.get("/v1/orders", headers=alice)
    patched = client.patch(path, json={"provider_name": "Ione Vantongeren DO"}, headers=alice)

    for body in (single.json(), listed.json()["items"][0], patched.json()):
        assert body["evidence"] == []
        assert body["flags"] == []
        assert body["document_id"] is None
    assert document_id not in single.text + listed.text + patched.text

    assert client.delete(path, headers=alice).status_code == 204
    db.expire_all()
    assert db.get(Document, document_id).content == b"%PDF-bob"


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
