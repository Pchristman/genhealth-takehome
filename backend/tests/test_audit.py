from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app.auth import Principal
from app.models import AuditEvent, Document, Order, OrderStatus
from app.orders import get_order
from tests.test_orders import ORDER, attach_document, create_order, set_status

ALICE = "alice@example.com"
BOB = "bob@example.com"


def audit_rows(db) -> list[tuple[str, str]]:
    # The rollback drops anything added but never committed, so only committed rows count.
    db.rollback()
    rows = db.exec(select(AuditEvent).order_by(AuditEvent.created_at)).all()
    return [(row.action, row.resource_id) for row in rows]


def latest_audit(db) -> AuditEvent:
    return db.exec(select(AuditEvent).order_by(AuditEvent.created_at.desc())).first()


def user_id(client, headers) -> str:
    return client.get("/auth/me", headers=headers).json()["id"]


ROUTES = [
    ("POST", "", ORDER, "order.create"),
    ("GET", "/{id}", None, "order.read"),
    ("GET", "", None, "order.read"),
    ("PATCH", "/{id}", {"provider_name": "Ione Vantongeren DO"}, "order.update"),
    ("DELETE", "/{id}", None, "order.delete"),
]


@pytest.mark.parametrize(("method", "suffix", "body", "action"), ROUTES)
def test_every_order_route_writes_an_audit_event(
    client, db, auth_headers, method, suffix, body, action
):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    before = audit_rows(db)

    url = "/v1/orders" + suffix.format(id=order_id)
    response = client.request(method, url, json=body, headers=headers)

    assert response.status_code < 300
    resource_id = response.json()["id"] if action == "order.create" else order_id
    assert audit_rows(db)[len(before) :] == [(action, resource_id)]
    latest = latest_audit(db)
    assert latest.actor_id == user_id(client, headers)
    assert latest.resource_type == "order"
    assert latest.request_id == response.headers["X-Request-ID"]


def test_list_audits_each_returned_row(client, db, auth_headers):
    headers = auth_headers()
    ids = [create_order(client, headers)["id"] for _ in range(3)]
    before = audit_rows(db)

    page = client.get("/v1/orders", params={"limit": 2}, headers=headers).json()

    returned = [order["id"] for order in page["items"]]
    assert len(returned) == 2 and set(returned) <= set(ids)
    new_rows = audit_rows(db)[len(before) :]
    assert sorted(new_rows) == sorted(("order.read", id_) for id_ in returned)


def test_empty_list_writes_no_audit(client, db, auth_headers):
    headers = auth_headers()
    assert client.get("/v1/orders", headers=headers).json()["items"] == []
    assert audit_rows(db) == []


@pytest.mark.parametrize(
    ("method", "body"), [("GET", None), ("PATCH", {"provider_name": "Intruder"}), ("DELETE", None)]
)
def test_foreign_access_audits_access_denied(client, db, auth_headers, method, body):
    alice, bob = auth_headers(ALICE), auth_headers(BOB)
    order_id = create_order(client, alice)["id"]
    before = audit_rows(db)

    client.request(method, f"/v1/orders/{order_id}", json=body, headers=bob)

    assert audit_rows(db)[len(before) :] == [("order.access_denied", order_id)]
    assert latest_audit(db).actor_id == user_id(client, bob)


def test_missing_id_audits_access_denied(client, db, auth_headers):
    missing = str(uuid4())
    client.get(f"/v1/orders/{missing}", headers=auth_headers())
    assert audit_rows(db) == [("order.access_denied", missing)]


def test_malformed_id_is_rejected_before_it_can_reach_the_audit_table(client, db, auth_headers):
    # The audit table holds ids only, and a caller can put anything in a URL.
    response = client.get("/v1/orders/Zephyrine-Quillfeather", headers=auth_headers())
    assert response.status_code == 422
    assert "Zephyrine" not in response.text
    assert audit_rows(db) == []


@pytest.mark.parametrize("flushed", [False, True])
def test_a_miss_never_commits_the_callers_pending_rows(client, engine, auth_headers, flushed):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    caller = user_id(client, headers)
    missing = str(uuid4())

    with Session(engine) as session:
        session.get(Order, order_id).provider_name = "Never committed"
        session.add(
            AuditEvent(
                actor_id=caller, action="order.pending", resource_type="order", resource_id="x"
            )
        )
        if flushed:
            session.flush()
        with pytest.raises(HTTPException) as caught:
            get_order(session, Principal(user_id=caller, email=ALICE), missing)

    assert caught.value.status_code == 404
    with Session(engine) as session:
        assert session.get(Order, order_id).provider_name == ORDER["provider_name"]
        actions = [row.action for row in session.exec(select(AuditEvent))]
    assert "order.pending" not in actions
    assert actions[-1] == "order.access_denied"


def test_no_change_patch_audits_a_read_not_an_update(client, db, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    before = audit_rows(db)

    response = client.patch(
        f"/v1/orders/{order_id}",
        json={"provider_name": ORDER["provider_name"]},
        headers=headers,
    )

    assert response.status_code == 200
    assert audit_rows(db)[len(before) :] == [("order.read", order_id)]


def test_refused_patch_on_a_denied_order_writes_no_update_audit(client, db, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    set_status(db, order_id, OrderStatus.denied, reviewed_by="reviewer-id")
    before = audit_rows(db)

    response = client.patch(
        f"/v1/orders/{order_id}", json={"provider_name": "Changed"}, headers=headers
    )

    assert response.status_code == 409
    assert audit_rows(db)[len(before) :] == []


def test_audit_rows_contain_no_patient_values(client, db, auth_headers):
    alice, bob = auth_headers(ALICE), auth_headers(BOB)
    order_id = create_order(client, alice)["id"]
    path = f"/v1/orders/{order_id}"
    client.get(path, headers=alice)
    client.get("/v1/orders", headers=alice)
    client.patch(path, json={"patient_last_name": "Brackenridge"}, headers=alice)
    client.get(path, headers=bob)
    client.delete(path, headers=alice)

    rows = db.exec(select(AuditEvent)).all()

    assert len(rows) == 6
    for row in rows:
        for column, stored in row.model_dump().items():
            for value in [*ORDER.values(), "Brackenridge"]:
                assert value not in str(stored), column


def block_commits(engine, event: str) -> None:
    """Make SQLite itself refuse the COMMIT of any transaction containing `event`.

    The trigger adds a row breaking a deferred foreign key. The writes succeed and COMMIT fails,
    so this is a real commit failure. It follows the row rather than failing the first commit, so
    a route that committed one half of its work first is caught leaving that half behind.
    """
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE gate (id TEXT PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE blocker (id TEXT REFERENCES gate(id) DEFERRABLE INITIALLY DEFERRED)"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER block_commit AFTER {event} "
            "BEGIN INSERT INTO blocker VALUES (NEW.id); END"
        )


def block_commits_holding_an_audit_row(engine) -> None:
    block_commits(engine, "INSERT ON auditevent")  # catches a change committed before its audit


def block_commits_updating_an_order(engine) -> None:
    block_commits(engine, 'UPDATE ON "order"')  # catches an audit committed before its change


def unblock_commits(engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER block_commit")


def persisted(engine) -> dict:
    """What the next request would see, including a transaction left open on a pooled connection."""
    with Session(engine) as session:
        return {
            "orders": [
                (order.id, order.provider_name, order.deleted_at)
                for order in session.exec(select(Order))
            ],
            "documents": [document.content for document in session.exec(select(Document))],
            "audits": [(row.action, row.resource_id) for row in session.exec(select(AuditEvent))],
        }


@pytest.mark.parametrize(
    ("method", "suffix", "body", "success"),
    [
        ("POST", "", ORDER, 201),
        ("PATCH", "/{id}", {"provider_name": "Ione Vantongeren DO"}, 200),
        ("DELETE", "/{id}", None, 204),
    ],
)
def test_audit_written_in_same_transaction(
    client, engine, auth_headers, method, suffix, body, success
):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    with Session(engine) as session:
        attach_document(session, order_id)
    url = "/v1/orders" + suffix.format(id=order_id)
    before = persisted(engine)
    block_commits_holding_an_audit_row(engine)

    failed = client.request(method, url, json=body, headers=headers)

    assert failed.status_code == 500
    assert persisted(engine) == before

    # Control: the same request lands once commits work, so `persisted` can see this change.
    unblock_commits(engine)
    assert client.request(method, url, json=body, headers=headers).status_code == success
    after = persisted(engine)
    assert after != before
    assert len(after["audits"]) == len(before["audits"]) + 1


@pytest.mark.parametrize(
    ("method", "body", "success"),
    [("PATCH", {"provider_name": "Ione Vantongeren DO"}, 200), ("DELETE", None, 204)],
)
def test_no_audit_persists_when_the_order_change_cannot_commit(
    client, engine, auth_headers, method, body, success
):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    with Session(engine) as session:
        attach_document(session, order_id)
    url = f"/v1/orders/{order_id}"
    before = persisted(engine)
    block_commits_updating_an_order(engine)

    failed = client.request(method, url, json=body, headers=headers)

    assert failed.status_code == 500
    assert persisted(engine) == before

    # Control: with the gate lifted the same request lands, so the gate was what stopped it.
    unblock_commits(engine)
    assert client.request(method, url, json=body, headers=headers).status_code == success
    assert len(persisted(engine)["audits"]) == len(before["audits"]) + 1


def test_read_returns_nothing_when_its_audit_cannot_commit(client, engine, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    block_commits_holding_an_audit_row(engine)

    for url in (f"/v1/orders/{order_id}", "/v1/orders"):
        response = client.get(url, headers=headers)
        assert response.status_code == 500
        assert ORDER["patient_last_name"] not in response.text
