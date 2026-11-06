from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, select

from app import review
from app.db import get_session, make_engine
from app.main import app
from app.models import AuditEvent, Document, FieldEvidence, Order, OrderStatus, ReviewFlag
from app.orders import get_order
from tests.test_audit import block_commits_holding_an_audit_row, persisted
from tests.test_orders import ORDER, attach_document, create_order, set_status

ALICE = "alice@example.com"
BOB = "bob@example.com"
PRINTED_NPI = "9182734556"
CORRECTED_NPI = "1234567893"
NEW_PROVIDER = "Ione Vantongeren DO"


def post_review(client, headers, order_id: str, decision: str = "approve"):
    url = f"/v1/orders/{order_id}/review"
    return client.post(url, json={"decision": decision}, headers=headers)


def user_id(client, headers) -> str:
    return client.get("/auth/me", headers=headers).json()["id"]


def actions(db) -> list[tuple[str, str, str]]:
    db.rollback()
    rows = db.exec(select(AuditEvent)).all()
    return [(row.action, row.resource_type, row.resource_id) for row in rows]


def add_flag(db, order_id: str, field: str | None, code: str) -> str:
    owner = db.get(Order, order_id).owner_id
    flag = ReviewFlag(owner_id=owner, order_id=order_id, field=field, code=code)
    flag_id = flag.id
    db.add(flag)
    db.commit()
    return flag_id


def add_evidence(db, order_id: str, field: str, printed: str) -> None:
    db.add(
        FieldEvidence(
            owner_id=db.get(Order, order_id).owner_id,
            order_id=order_id,
            field=field,
            extracted_value=printed,
            source_quote=f"NPI: {printed}",
            page=2,
            verification="unverified",
        )
    )
    db.commit()


def clear_field(db, order_id: str, field: str) -> None:
    order = db.get(Order, order_id)
    setattr(order, field, None)
    db.add(order)
    db.commit()


def test_person_can_deny(client, db, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]

    response = post_review(client, headers, order_id, "deny")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "denied"
    assert body["reviewed_by"] == user_id(client, headers)
    assert body["reviewed_at"].endswith("Z")
    assert ("order.review", "order", order_id) in actions(db)


def test_person_can_approve(client, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]

    body = post_review(client, headers, order_id).json()

    assert body["status"] == "approved"
    assert body["reviewed_by"] == user_id(client, headers)
    assert client.get(f"/v1/orders/{order_id}", headers=headers).json()["status"] == "approved"


def test_approve_with_open_flags_keeps_flags(client, db, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    flag_id = add_flag(db, order_id, "provider_npi", "invalid_npi")

    body = post_review(client, headers, order_id).json()

    assert body["status"] == "approved"
    assert [(flag["id"], flag["resolved_at"]) for flag in body["flags"]] == [(flag_id, None)]


@pytest.mark.parametrize("field", sorted(ORDER))
def test_approve_with_null_field_returns_409(client, db, auth_headers, field):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    clear_field(db, order_id, field)

    response = post_review(client, headers, order_id)

    assert response.status_code == 409
    assert response.json() == {"detail": review.MISSING_FIELDS}
    assert client.get(f"/v1/orders/{order_id}", headers=headers).json()["status"] == "needs_review"


def test_person_can_deny_an_order_with_null_fields(client, db, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    clear_field(db, order_id, "provider_npi")

    assert post_review(client, headers, order_id, "deny").json()["status"] == "denied"


@pytest.mark.parametrize("status", [OrderStatus.approved, OrderStatus.denied])
@pytest.mark.parametrize("decision", ["approve", "deny"])
def test_review_requires_needs_review_status(client, db, auth_headers, status, decision):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    set_status(db, order_id, status, reviewed_by="first-reviewer")

    response = post_review(client, headers, order_id, decision)

    assert response.status_code == 409
    assert response.json() == {"detail": review.NOT_REVIEWABLE}
    db.rollback()
    order = db.get(Order, order_id)
    assert order.status == status
    assert order.reviewed_by == "first-reviewer"
    assert not [row for row in actions(db) if row[0] == "order.review"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"decision": "maybe"},
        {"decision": "APPROVE"},
        {"decision": None},
        {"decision": "approve", "status": "approved"},
        {"decision": "approve", "reviewed_by": "someone"},
    ],
)
def test_review_rejects_a_bad_body(client, auth_headers, body):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]

    response = client.post(f"/v1/orders/{order_id}/review", json=body, headers=headers)

    assert response.status_code == 422
    assert client.get(f"/v1/orders/{order_id}", headers=headers).json()["status"] == "needs_review"


def test_other_user_cannot_review_or_download(client, db, auth_headers):
    alice, bob = auth_headers(ALICE), auth_headers(BOB)
    order_id = create_order(client, alice)["id"]
    attach_document(db, order_id)
    missing = str(uuid4())

    foreign_review = post_review(client, bob, order_id)
    missing_review = post_review(client, bob, missing)
    foreign_download = client.get(f"/v1/orders/{order_id}/document", headers=bob)
    missing_download = client.get(f"/v1/orders/{missing}/document", headers=bob)

    for response in (foreign_review, missing_review, foreign_download, missing_download):
        assert response.status_code == 404
        assert response.json() == {"detail": "Order not found."}
    assert client.get(f"/v1/orders/{order_id}", headers=alice).json()["status"] == "needs_review"
    assert ("order.access_denied", "order", order_id) in actions(db)
    assert not [row for row in actions(db) if row[0] in ("order.review", "document.read")]


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [("POST", "review", {"decision": "approve"}), ("GET", "document", None)],
)
def test_review_and_document_need_a_token(client, method, suffix, body):
    url = f"/v1/orders/{uuid4()}/{suffix}"
    assert client.request(method, url, json=body).status_code == 401


def test_patch_resolves_field_flags_and_keeps_extracted_value(client, db, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers, provider_npi=CORRECTED_NPI)["id"]
    add_evidence(db, order_id, "provider_npi", PRINTED_NPI)
    add_evidence(db, order_id, "provider_name", "Dr Who Knows")
    add_flag(db, order_id, "provider_npi", "invalid_npi")
    add_flag(db, order_id, "provider_npi", "unverified_quote")
    other_field = add_flag(db, order_id, "provider_name", "unverified_quote")
    whole_order = add_flag(db, order_id, None, "multiple_patients")
    me = user_id(client, headers)

    response = client.patch(
        f"/v1/orders/{order_id}", json={"provider_npi": "1245319599"}, headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["provider_npi"] == "1245319599"
    flags = {flag["id"]: flag for flag in body["flags"]}
    assert flags.pop(other_field)["resolved_at"] is None
    assert flags.pop(whole_order)["resolved_at"] is None
    assert len(flags) == 2
    assert all(flag["resolved_by"] == me and flag["resolved_at"] for flag in flags.values())
    evidence = {row["field"]: row for row in body["evidence"]}
    assert evidence["provider_npi"]["corrected"] is True
    assert evidence["provider_npi"]["extracted_value"] == PRINTED_NPI
    assert evidence["provider_npi"]["source_quote"] == f"NPI: {PRINTED_NPI}"
    assert evidence["provider_npi"]["page"] == 2
    assert evidence["provider_npi"]["verification"] == "unverified"
    assert evidence["provider_name"]["corrected"] is False


def test_patch_does_not_reresolve_a_resolved_flag(client, db, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    flag_id = add_flag(db, order_id, "provider_name", "illegible")
    url = f"/v1/orders/{order_id}"

    client.patch(url, json={"provider_name": NEW_PROVIDER}, headers=headers)
    first = client.get(url, headers=headers).json()["flags"][0]["resolved_at"]
    client.patch(url, json={"provider_name": "Ione Vantongeren MD"}, headers=headers)

    db.rollback()
    assert client.get(url, headers=headers).json()["flags"][0]["resolved_at"] == first
    assert db.get(ReviewFlag, flag_id).resolved_at is not None


def test_patch_with_the_same_value_corrects_nothing(client, db, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    add_evidence(db, order_id, "provider_name", ORDER["provider_name"])
    add_flag(db, order_id, "provider_name", "unverified_quote")

    response = client.patch(
        f"/v1/orders/{order_id}", json={"provider_name": ORDER["provider_name"]}, headers=headers
    )

    body = response.json()
    assert body["flags"][0]["resolved_at"] is None
    assert body["evidence"][0]["corrected"] is False


def test_order_out_shows_evidence_and_that_a_pdf_exists(client, db, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    add_evidence(db, order_id, "provider_npi", PRINTED_NPI)
    document_id = attach_document(db, order_id)

    body = client.get(f"/v1/orders/{order_id}", headers=headers).json()

    assert body["document_id"] == document_id
    row = body["evidence"][0]
    assert (row["verification"], row["source_quote"], row["page"]) == (
        "unverified",
        f"NPI: {PRINTED_NPI}",
        2,
    )


def test_document_download_audited(client, db, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    document_id = attach_document(db, order_id)

    response = client.get(f"/v1/orders/{order_id}/document", headers=headers)

    assert response.status_code == 200
    assert response.content == b"%PDF-synthetic"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'inline; filename="order.pdf"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert ("document.read", "document", document_id) in actions(db)


def test_document_is_404_for_an_order_without_a_pdf(client, db, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]

    assert client.get(f"/v1/orders/{order_id}/document", headers=headers).status_code == 404
    assert not [row for row in actions(db) if row[0] == "document.read"]


def test_document_is_404_when_bytes_are_cleared(client, db, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    document_id = attach_document(db, order_id)
    document = db.get(Document, document_id)
    document.content = None
    db.add(document)
    db.commit()

    assert client.get(f"/v1/orders/{order_id}/document", headers=headers).status_code == 404
    assert not [row for row in actions(db) if row[0] == "document.read"]


def test_document_is_404_after_the_order_is_deleted(client, db, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    attach_document(db, order_id)

    assert client.delete(f"/v1/orders/{order_id}", headers=headers).status_code == 204

    assert client.get(f"/v1/orders/{order_id}/document", headers=headers).status_code == 404


def test_document_ignores_a_link_to_someone_elses_document(client, db, auth_headers):
    alice, bob = auth_headers(ALICE), auth_headers(BOB)
    theirs = create_order(client, alice)["id"]
    document_id = attach_document(db, theirs)
    mine = create_order(client, bob)["id"]
    order = db.get(Order, mine)
    order.document_id = document_id
    db.add(order)
    db.commit()

    assert client.get(f"/v1/orders/{mine}/document", headers=bob).status_code == 404


@pytest.fixture
def file_engine(client, tmp_path):
    """One connection per request: a rival session can then commit between two steps of a route."""
    engine = make_engine(f"sqlite:///{tmp_path / 'review.db'}")
    SQLModel.metadata.create_all(engine)

    def session_per_request():
        with Session(engine) as session:
            try:
                yield session
            except Exception:
                session.rollback()
                raise

    app.dependency_overrides[get_session] = session_per_request
    yield engine
    engine.dispose()


def race_after_load(monkeypatch, engine, change) -> None:
    """Commit `change` from a second session after the route loads the Order, before it writes."""

    def racing_get_order(db, who, order_id):
        order = get_order(db, who, order_id)
        with Session(engine) as rival:
            row = rival.get(Order, order_id)
            change(row)
            rival.add(row)
            rival.commit()
        return order

    monkeypatch.setattr(review, "get_order", racing_get_order)


def test_review_rejects_a_stale_write(client, file_engine, auth_headers, monkeypatch):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]

    def rival_denies(row: Order) -> None:
        row.status = OrderStatus.denied
        row.reviewed_by = "rival-reviewer"
        row.reviewed_at = datetime.now(UTC)

    race_after_load(monkeypatch, file_engine, rival_denies)

    response = post_review(client, headers, order_id, "approve")

    assert response.status_code == 409
    with Session(file_engine) as session:
        stored = session.get(Order, order_id)
        assert stored.status == OrderStatus.denied
        assert stored.reviewed_by == "rival-reviewer"
        audits = session.exec(select(AuditEvent)).all()
        assert "order.review" not in {row.action for row in audits}


def test_review_cannot_resurrect_an_order_deleted_mid_review(
    client, file_engine, auth_headers, monkeypatch
):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]

    def rival_deletes(row: Order) -> None:
        row.deleted_at = datetime.now(UTC)

    race_after_load(monkeypatch, file_engine, rival_deletes)

    assert post_review(client, headers, order_id).status_code == 409

    with Session(file_engine) as session:
        assert session.get(Order, order_id).status == OrderStatus.needs_review


@pytest.mark.skipif(not hasattr(Order, "version"), reason="needs the version column")
def test_review_rejects_an_edit_that_landed_mid_review(
    client, file_engine, auth_headers, monkeypatch
):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]

    def rival_edits(row: Order) -> None:
        row.provider_name = NEW_PROVIDER

    race_after_load(monkeypatch, file_engine, rival_edits)

    assert post_review(client, headers, order_id).status_code == 409

    with Session(file_engine) as session:
        stored = session.get(Order, order_id)
        assert stored.status == OrderStatus.needs_review
        assert stored.provider_name == NEW_PROVIDER


def test_review_is_undone_when_its_audit_cannot_commit(client, file_engine, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    before = persisted(file_engine)
    block_commits_holding_an_audit_row(file_engine)

    response = post_review(client, headers, order_id)

    assert response.status_code == 500
    assert persisted(file_engine) == before
    with Session(file_engine) as session:
        assert session.get(Order, order_id).status == OrderStatus.needs_review


def test_document_returns_no_bytes_when_its_audit_cannot_commit(client, file_engine, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    with Session(file_engine) as session:
        attach_document(session, order_id)
    block_commits_holding_an_audit_row(file_engine)

    response = client.get(f"/v1/orders/{order_id}/document", headers=headers)

    assert response.status_code == 500
    assert b"%PDF" not in response.content


def test_patch_writes_the_order_once(client, file_engine, auth_headers):
    # A second UPDATE would move the version column twice for one edit.
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    with Session(file_engine) as session:
        add_flag(session, order_id, "provider_name", "illegible")
    updates: list[str] = []

    def count_order_updates(_conn, _cursor, statement, *_rest) -> None:
        if statement.startswith('UPDATE "order"'):
            updates.append(statement)

    event.listen(file_engine, "before_cursor_execute", count_order_updates)

    response = client.patch(
        f"/v1/orders/{order_id}", json={"provider_name": NEW_PROVIDER}, headers=headers
    )

    assert response.status_code == 200
    assert len(updates) == 1
