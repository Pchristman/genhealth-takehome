from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models import Document, FieldEvidence, Order, OrderStatus, ReviewFlag

ORDER = {
    "patient_first_name": "Zephyrine",
    "patient_last_name": "Quillfeather",
    "patient_dob": "1961-02-17",
    "provider_name": "Thaddeus Marrowby MD",
    "provider_npi": "1234567893",
}


def create_order(client, headers, **changes) -> dict:
    response = client.post("/v1/orders", json={**ORDER, **changes}, headers=headers)
    assert response.status_code == 201
    return response.json()


def set_status(db, order_id: str, status: OrderStatus, reviewed_by: str | None = None) -> None:
    order = db.get(Order, order_id)
    order.status = status
    order.reviewed_by = reviewed_by
    order.reviewed_at = datetime.now(UTC) if reviewed_by else None
    db.add(order)
    db.commit()


def attach_document(db, order_id: str) -> str:
    order = db.get(Order, order_id)
    document = Document(
        owner_id=order.owner_id,
        sha256="0" * 64,
        byte_size=14,
        page_count=1,
        text_pages=[1],
        content=b"%PDF-synthetic",
    )
    db.add(document)
    order.document_id = document.id
    db.add(order)
    db.commit()
    return document.id


def test_create_order_starts_needs_review(client, auth_headers):
    response = client.post("/v1/orders", json=ORDER, headers=auth_headers())
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "needs_review"
    assert body["source"] == "manual"
    assert {key: body[key] for key in ORDER} == ORDER
    assert body["created_at"].endswith("Z")


@pytest.mark.parametrize("field", sorted(ORDER))
def test_create_requires_every_field(client, auth_headers, field):
    body = {key: value for key, value in ORDER.items() if key != field}
    assert client.post("/v1/orders", json=body, headers=auth_headers()).status_code == 422


@pytest.mark.parametrize("value", [None, "", "   "])
def test_create_rejects_blank_field(client, auth_headers, value):
    body = {**ORDER, "patient_last_name": value}
    assert client.post("/v1/orders", json=body, headers=auth_headers()).status_code == 422


def test_create_rejects_wrong_types(client, auth_headers):
    headers = auth_headers()
    for field, value in [("patient_dob", "not-a-date"), ("provider_npi", 1234567893)]:
        body = {**ORDER, field: value}
        assert client.post("/v1/orders", json=body, headers=headers).status_code == 422


def test_get_update_delete_round_trip(client, auth_headers):
    headers = auth_headers()
    order = create_order(client, headers)
    path = f"/v1/orders/{order['id']}"

    fetched = client.get(path, headers=headers)
    assert fetched.status_code == 200
    assert fetched.json() == order

    patched = client.patch(path, json={"patient_first_name": "Ottoline"}, headers=headers)
    assert patched.status_code == 200
    assert patched.json()["patient_first_name"] == "Ottoline"
    assert patched.json()["patient_last_name"] == ORDER["patient_last_name"]
    assert patched.json()["updated_at"] > order["updated_at"]
    assert client.get(path, headers=headers).json() == patched.json()

    deleted = client.delete(path, headers=headers)
    assert deleted.status_code == 204
    assert client.get(path, headers=headers).status_code == 404


def test_order_out_returns_evidence_and_flags_without_owner(client, db, auth_headers):
    headers = auth_headers()
    order = create_order(client, headers)
    assert order["evidence"] == [] and order["flags"] == []

    owner_id = db.get(Order, order["id"]).owner_id
    db.add(
        FieldEvidence(
            owner_id=owner_id,
            order_id=order["id"],
            field="provider_npi",
            extracted_value=ORDER["provider_npi"],
            source_quote=f"NPI {ORDER['provider_npi']}",
            page=1,
            verification="verified",
        )
    )
    db.add(
        ReviewFlag(owner_id=owner_id, order_id=order["id"], field="provider_npi", code="missing")
    )
    db.commit()

    single = client.get(f"/v1/orders/{order['id']}", headers=headers)
    listed = client.get("/v1/orders", headers=headers).json()["items"][0]
    for body in (single.json(), listed):
        assert [row["source_quote"] for row in body["evidence"]] == ["NPI 1234567893"]
        assert [row["code"] for row in body["flags"]] == ["missing"]
    assert "owner_id" not in single.text


def test_list_is_paged_and_filters_by_status(client, db, auth_headers):
    headers = auth_headers()
    orders = [create_order(client, headers) for _ in range(101)]

    capped = client.get("/v1/orders", params={"limit": 500}, headers=headers).json()
    assert capped["limit"] == 100
    assert capped["total"] == 101
    assert len(capped["items"]) == 100

    # Identical timestamps: only the id tiebreak keeps pages from repeating or skipping rows.
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    for order in orders:
        row = db.get(Order, order["id"])
        row.created_at = stamp
        db.add(row)
    db.commit()
    seen = []
    for offset in range(0, 101, 40):
        params = {"limit": 40, "offset": offset}
        seen += client.get("/v1/orders", params=params, headers=headers).json()["items"]
    assert [order["id"] for order in seen] == sorted((o["id"] for o in orders), reverse=True)

    set_status(db, orders[0]["id"], OrderStatus.approved)
    approved = client.get("/v1/orders", params={"status": "approved"}, headers=headers).json()
    assert [order["id"] for order in approved["items"]] == [orders[0]["id"]]
    assert approved["total"] == 1


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"offset": -1}, {"offset": 2**63}, {"status": "pending"}]
)
def test_list_rejects_bad_paging_and_status(client, auth_headers, params):
    assert client.get("/v1/orders", params=params, headers=auth_headers()).status_code == 422


def test_patch_on_approved_returns_to_needs_review(client, db, auth_headers):
    headers = auth_headers()
    order = create_order(client, headers)
    set_status(db, order["id"], OrderStatus.approved, reviewed_by="reviewer-id")

    response = client.patch(
        f"/v1/orders/{order['id']}", json={"provider_name": "Ione Vantongeren DO"}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "needs_review"
    # The approval was for the old values, so its stamp must not outlive them.
    assert response.json()["reviewed_by"] is None
    assert response.json()["reviewed_at"] is None


def test_patch_on_denied_stays_denied(client, db, auth_headers):
    headers = auth_headers()
    order = create_order(client, headers)
    set_status(db, order["id"], OrderStatus.denied, reviewed_by="reviewer-id")

    response = client.patch(
        f"/v1/orders/{order['id']}", json={"provider_name": "Ione Vantongeren DO"}, headers=headers
    )

    assert response.json()["status"] == "denied"


def test_deleted_order_returns_404_and_clears_pdf_bytes(client, db, auth_headers):
    headers = auth_headers()
    order = create_order(client, headers)
    document_id = attach_document(db, order["id"])
    path = f"/v1/orders/{order['id']}"

    assert client.delete(path, headers=headers).status_code == 204

    assert client.get(path, headers=headers).status_code == 404
    assert client.patch(path, json={"provider_name": "X"}, headers=headers).status_code == 404
    assert client.delete(path, headers=headers).status_code == 404
    assert client.get("/v1/orders", headers=headers).json()["items"] == []
    db.expire_all()
    assert db.get(Document, document_id).content is None
    assert db.get(Order, order["id"]).deleted_at is not None  # soft: the row stays


@pytest.mark.parametrize(
    "extra", [{"status": "approved"}, {"owner_id": "someone-else"}, {"source": "upload"}]
)
def test_patch_cannot_set_status_or_owner(client, auth_headers, extra):
    headers = auth_headers()
    order = create_order(client, headers)
    path = f"/v1/orders/{order['id']}"

    assert client.patch(path, json=extra, headers=headers).status_code == 422
    assert client.post("/v1/orders", json={**ORDER, **extra}, headers=headers).status_code == 422

    assert client.get(path, headers=headers).json() == order


@pytest.mark.parametrize("value", [None, "", "   "])
def test_patch_cannot_blank_a_field(client, auth_headers, value):
    headers = auth_headers()
    order = create_order(client, headers)
    path = f"/v1/orders/{order['id']}"

    response = client.patch(path, json={"patient_first_name": value}, headers=headers)

    assert response.status_code == 422
    assert client.get(path, headers=headers).json() == order


def test_validation_errors_do_not_echo_patient_values(client, auth_headers):
    headers = auth_headers()
    response = client.post(
        "/v1/orders", json={**ORDER, "patient_dob": "Zephyrine-bad-date"}, headers=headers
    )
    assert response.status_code == 422
    assert "Zephyrine" not in response.text
    assert ORDER["provider_npi"] not in response.text


def test_unknown_id_returns_404(client, auth_headers):
    assert client.get(f"/v1/orders/{uuid4()}", headers=auth_headers()).status_code == 404
