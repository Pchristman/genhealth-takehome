"""An approved Order is final: no edit, no reopening, no delete, whoever approved it."""

import pytest
from sqlmodel import select

from app.main import SAFE_MESSAGE_TYPES
from app.models import AuditEvent, Document, FieldEvidence, Order, OrderStatus, ReviewFlag
from app.orders import APPROVED_NOT_DELETABLE, APPROVED_NOT_EDITABLE
from tests import pdf_fixtures as fx
from tests.llm_stub import clean_result, reply
from tests.test_orders import ORDER, attach_document, create_order, set_status
from tests.test_review import add_evidence, add_flag, post_review, user_id
from tests.test_upload import upload

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


def audit_rows(db, order_id: str) -> list[tuple[str, str]]:
    db.rollback()
    rows = db.exec(select(AuditEvent).where(AuditEvent.resource_id == order_id))
    return [(row.action, row.actor_id) for row in rows]


def test_delete_on_approved_is_refused(client, db, auth_headers):
    headers = auth_headers()
    order_id = approved_order(client, db, headers)["id"]
    document_id = attach_document(db, order_id)
    path = f"/v1/orders/{order_id}"

    response = client.delete(path, headers=headers)

    assert response.status_code == 409
    assert response.json() == {"detail": APPROVED_NOT_DELETABLE}
    assert client.get(path, headers=headers).status_code == 200
    db.rollback()
    assert db.get(Order, order_id).deleted_at is None
    assert db.get(Document, document_id).content is not None
    assert client.get(f"{path}/document", headers=headers).status_code == 200
    listed = client.get("/v1/orders", params={"status": "approved"}, headers=headers).json()
    assert order_id in [order["id"] for order in listed["items"]]


def test_refused_write_records_nothing(client, db, auth_headers):
    headers = auth_headers()
    order_id = approved_order(client, db, headers)["id"]
    path = f"/v1/orders/{order_id}"
    before = audit_rows(db, order_id)

    assert (
        client.patch(path, json={"provider_name": NEW_PROVIDER}, headers=headers).status_code == 409
    )
    assert client.delete(path, headers=headers).status_code == 409

    assert audit_rows(db, order_id) == before


def test_someone_elses_approved_order_is_404(client, db, auth_headers):
    alice, bob = auth_headers("alice@example.com"), auth_headers("bob@example.com")
    before = approved_order(client, db, alice)
    path = f"/v1/orders/{before['id']}"
    bob_id = user_id(client, bob)

    patched = client.patch(path, json={"provider_name": NEW_PROVIDER}, headers=bob)
    deleted = client.delete(path, headers=bob)

    assert (patched.status_code, deleted.status_code) == (404, 404)
    assert client.get(path, headers=alice).json() == before
    denied = [row for row in audit_rows(db, before["id"]) if row[0] == "order.access_denied"]
    assert denied == [("order.access_denied", bob_id)] * 2


def test_denied_order_can_still_be_deleted(client, db, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    set_status(db, order_id, OrderStatus.denied, reviewed_by="reviewer-id")

    assert client.delete(f"/v1/orders/{order_id}", headers=headers).status_code == 204


def test_needs_review_order_can_still_be_deleted(client, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]

    assert client.delete(f"/v1/orders/{order_id}", headers=headers).status_code == 204


def approve_by_code(client, headers, stub_llm) -> dict:
    stub_llm(reply(clean_result()))
    return upload(client, headers).json()


def approve_upload_by_person(client, headers, stub_llm) -> dict:
    stub_llm(reply(clean_result()))
    order = upload(client, headers, fx.scan_pdf()).json()
    assert order["status"] == "needs_review"
    return post_review(client, headers, order["id"], "approve").json()


def approve_manual_by_person(client, headers, stub_llm) -> dict:
    order = create_order(client, headers)
    return post_review(client, headers, order["id"], "approve").json()


APPROVAL_PATHS = {
    "code": (approve_by_code, "upload", False),
    "person_upload": (approve_upload_by_person, "upload", True),
    "person_manual": (approve_manual_by_person, "manual", True),
}
ATTEMPTS = {
    "patch_changed": ("PATCH", {"provider_name": NEW_PROVIDER}, APPROVED_NOT_EDITABLE),
    "patch_empty": ("PATCH", {}, APPROVED_NOT_EDITABLE),
    "delete": ("DELETE", None, APPROVED_NOT_DELETABLE),
}


@pytest.mark.parametrize("attempt", ATTEMPTS)
@pytest.mark.parametrize("path_name", APPROVAL_PATHS)
def test_approved_order_is_final_whoever_approved_it(
    client, auth_headers, stub_llm, path_name, attempt
):
    headers = auth_headers()
    approve, source, by_person = APPROVAL_PATHS[path_name]
    method, body, detail = ATTEMPTS[attempt]
    order_id = approve(client, headers, stub_llm)["id"]
    url = f"/v1/orders/{order_id}"
    before = client.get(url, headers=headers).json()

    # The approver signal is a contract: null for code, the person and a time otherwise.
    assert before["status"] == "approved"
    assert before["source"] == source
    if by_person:
        assert before["reviewed_by"] == user_id(client, headers)
        assert before["reviewed_at"] is not None
    else:
        assert before["reviewed_by"] is None
        assert before["reviewed_at"] is None

    response = client.request(method, url, json=body, headers=headers)

    assert response.status_code == 409
    assert response.json() == {"detail": detail}
    assert client.get(url, headers=headers).json() == before


def test_only_a_clean_upload_is_approved_without_a_person(client, auth_headers, stub_llm):
    # Pins today's behaviour: `reviewed_by is None` on an approved Order means code approved it.
    headers = auth_headers()
    assert create_order(client, headers)["status"] == "needs_review"

    by_code = approve_by_code(client, headers, stub_llm)
    assert (by_code["status"], by_code["reviewed_by"]) == ("approved", None)
    assert {row["verification"] for row in by_code["evidence"]} == {"verified"}
    assert by_code["flags"] == []

    by_person = approve_manual_by_person(client, headers, stub_llm)
    assert by_person["reviewed_by"] == user_id(client, headers)
    assert by_person["reviewed_at"] is not None
