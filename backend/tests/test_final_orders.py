"""An approved Order is final: no edit, no reopening, no delete, whoever approved it."""

import pytest
from sqlmodel import select

from app.main import SAFE_MESSAGE_TYPES
from app.models import FieldEvidence, Order, OrderStatus, ReviewFlag
from app.orders import APPROVED_NOT_EDITABLE
from tests.test_orders import ORDER, create_order, set_status
from tests.test_review import add_evidence, add_flag

NEW_PROVIDER = "Ione Vantongeren DO"


def approved_order(client, db, headers, reviewed_by: str | None = "reviewer-id") -> dict:
    order_id = create_order(client, headers)["id"]
    set_status(db, order_id, OrderStatus.approved, reviewed_by=reviewed_by)
    return client.get(f"/v1/orders/{order_id}", headers=headers).json()


def stored_values() -> list[str]:
    return list(ORDER.values())


def stored_version(db, order_id: str) -> int:
    db.rollback()  # read what is committed, not what this session cached
    return db.get(Order, order_id).version


@pytest.mark.parametrize(
    "change",
    [
        {"provider_name": NEW_PROVIDER},
        {"provider_name": ORDER["provider_name"]},
        {"provider_name": f"  {ORDER['provider_name']} "},
        ORDER,
        {},
    ],
    ids=["changed", "same", "padded", "all_fields", "empty"],
)
def test_patch_on_approved_is_refused(client, db, auth_headers, change):
    headers = auth_headers()
    before = approved_order(client, db, headers)
    path = f"/v1/orders/{before['id']}"
    version = stored_version(db, before["id"])

    for _ in range(2):  # a repeated refusal must not drift either
        response = client.patch(path, json=change, headers=headers)
        assert response.status_code == 409
        assert response.json() == {"detail": APPROVED_NOT_EDITABLE}

    assert client.get(path, headers=headers).json() == before
    assert stored_version(db, before["id"]) == version


def test_refused_patch_leaves_flags_and_evidence_alone(client, db, auth_headers):
    headers = auth_headers()
    order_id = approved_order(client, db, headers)["id"]
    flag_id = add_flag(db, order_id, "provider_npi", "invalid_npi")
    add_evidence(db, order_id, "provider_npi", "9182734556")

    response = client.patch(
        f"/v1/orders/{order_id}", json={"provider_npi": "1245319599"}, headers=headers
    )

    assert response.status_code == 409
    db.rollback()
    assert db.get(ReviewFlag, flag_id).resolved_at is None
    evidence = db.exec(select(FieldEvidence).where(FieldEvidence.order_id == order_id)).one()
    assert evidence.corrected is False


def test_patch_cannot_set_status_on_approved(client, db, auth_headers):
    headers = auth_headers()
    order_id = approved_order(client, db, headers)["id"]

    response = client.patch(
        f"/v1/orders/{order_id}", json={"status": "needs_review"}, headers=headers
    )

    assert response.status_code == 422
    assert "approved" not in response.text
    assert not [value for value in stored_values() if value in response.text]
    db.rollback()
    assert db.get(Order, order_id).status == OrderStatus.approved


@pytest.mark.parametrize(
    "body",
    [{"provider_npi": "1234567890"}, {"patient_first_name": None}],
    ids=["bad_npi", "null_name"],
)
def test_bad_body_on_approved_is_422_and_changes_nothing(client, db, auth_headers, body):
    headers = auth_headers()
    before = approved_order(client, db, headers)
    path = f"/v1/orders/{before['id']}"

    response = client.patch(path, json=body, headers=headers)

    assert response.status_code == 422
    assert not [value for value in stored_values() if value in response.text]
    for submitted in body.values():
        if isinstance(submitted, str):
            assert submitted not in response.text
    assert all(
        error["type"] in SAFE_MESSAGE_TYPES or error["msg"] == "Invalid value."
        for error in response.json()["detail"]
    )
    assert client.get(path, headers=headers).json() == before


def test_needs_review_order_can_still_be_edited(client, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]

    response = client.patch(
        f"/v1/orders/{order_id}", json={"provider_name": NEW_PROVIDER}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "needs_review"
    assert response.json()["provider_name"] == NEW_PROVIDER
