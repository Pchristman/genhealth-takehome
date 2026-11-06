from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, select

from app.db import get_session, make_engine
from app.main import app
from app.models import AuditEvent, Document, Order
from tests.test_orders import ORDER, attach_document, create_order

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


@pytest.fixture
def engine(client, tmp_path):
    """A file database and one session per request, as in production.

    The shared `db` fixture can't prove what persisted: its single connection sees its own
    uncommitted rows, and a test has to roll it back by hand. Here each request runs alone.
    """
    file_engine = make_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    SQLModel.metadata.create_all(file_engine)

    def session_per_request():
        with Session(file_engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_per_request
    yield file_engine
    file_engine.dispose()


def block_commits_holding_an_audit_row(engine) -> None:
    """Make SQLite itself refuse the COMMIT of any transaction that inserted an audit row.

    The trigger adds a row breaking a deferred foreign key. The inserts succeed and COMMIT fails,
    so this is a real commit failure. It follows the audit row rather than failing the first
    commit, so a route that committed its change first would be caught leaving that change behind.
    """
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE gate (id TEXT PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE blocker (id TEXT REFERENCES gate(id) DEFERRABLE INITIALLY DEFERRED)"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER block_audit AFTER INSERT ON auditevent "
            "BEGIN INSERT INTO blocker VALUES (NEW.id); END"
        )


def unblock_commits(engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER block_audit")


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


def test_read_returns_nothing_when_its_audit_cannot_commit(client, engine, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    block_commits_holding_an_audit_row(engine)

    for url in (f"/v1/orders/{order_id}", "/v1/orders"):
        response = client.get(url, headers=headers)
        assert response.status_code == 500
        assert ORDER["patient_last_name"] not in response.text
