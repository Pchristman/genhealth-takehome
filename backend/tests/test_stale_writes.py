"""A PATCH or DELETE that loaded the row before another commit must not write over that commit."""

from collections.abc import Callable

import pytest
from sqlmodel import Session, select

from app import orders
from app.models import AuditEvent, Document, Order, OrderStatus
from app.orders import APPROVED_NOT_EDITABLE
from tests.test_orders import ORDER, attach_document, create_order, set_status

REVIEWER = "reviewer-id"
STALE = {"detail": "Order changed since it was loaded. Reload it and try again."}
PATCH = {"provider_name": "Ione Vantongeren DO"}


def interleave(monkeypatch, commit_between: Callable[[str], None]) -> None:
    """Run `commit_between` once, after a route loads its Order and before it writes."""
    real_get_order = orders.get_order

    def load_then_interleave(db, who, order_id):
        order = real_get_order(db, who, order_id)
        monkeypatch.setattr(orders, "get_order", real_get_order)
        commit_between(order_id)
        return order

    monkeypatch.setattr(orders, "get_order", load_then_interleave)


def stored(engine, order_id: str) -> Order:
    with Session(engine) as session:
        return session.get(Order, order_id)


def actions(engine, order_id: str, *names: str) -> list[str]:
    with Session(engine) as session:
        rows = session.exec(select(AuditEvent).where(AuditEvent.resource_id == order_id))
        return [row.action for row in rows if row.action in names]


@pytest.fixture
def approve_between(engine, monkeypatch):
    def approve(order_id: str) -> None:
        with Session(engine) as session:
            set_status(session, order_id, OrderStatus.approved, reviewed_by=REVIEWER)

    interleave(monkeypatch, approve)


def test_stale_patch_cannot_write_over_an_approval(client, engine, auth_headers, approve_between):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]

    response = client.patch(f"/v1/orders/{order_id}", json=PATCH, headers=headers)

    assert response.status_code == 409
    assert response.json() == STALE
    row = stored(engine, order_id)
    assert row.status == OrderStatus.approved
    assert row.reviewed_by == REVIEWER
    assert row.provider_name == ORDER["provider_name"]
    assert actions(engine, order_id, "order.update") == []


def test_stale_delete_cannot_remove_an_approved_order(
    client, engine, auth_headers, approve_between
):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    with Session(engine) as session:
        document_id = attach_document(session, order_id)

    response = client.delete(f"/v1/orders/{order_id}", headers=headers)

    assert response.status_code == 409
    assert response.json() == STALE
    assert stored(engine, order_id).deleted_at is None
    assert actions(engine, order_id, "order.delete") == []
    with Session(engine) as session:
        assert session.get(Document, document_id).content is not None


def test_stale_patch_cannot_write_over_a_delete(client, engine, auth_headers, monkeypatch):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    url = f"/v1/orders/{order_id}"
    interleave(monkeypatch, lambda _: client.delete(url, headers=headers))

    response = client.patch(url, json=PATCH, headers=headers)

    assert response.status_code == 409
    assert stored(engine, order_id).provider_name == ORDER["provider_name"]
    assert actions(engine, order_id, "order.update") == []


def test_racing_deletes_give_one_success_and_one_audit(client, engine, auth_headers, monkeypatch):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    url = f"/v1/orders/{order_id}"
    inner = []
    interleave(monkeypatch, lambda _: inner.append(client.delete(url, headers=headers)))

    outer = client.delete(url, headers=headers)

    assert inner[0].status_code == 204
    assert outer.status_code in (404, 409)
    assert actions(engine, order_id, "order.delete") == ["order.delete"]


def test_a_stale_write_leaves_the_connection_clean(client, engine, auth_headers, approve_between):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    url = f"/v1/orders/{order_id}"
    assert client.patch(url, json=PATCH, headers=headers).status_code == 409

    retried = client.patch(url, json=PATCH, headers=headers)

    # A poisoned session would repeat the stale-write message. A clean one reads the approval.
    assert retried.status_code == 409
    assert retried.json() == {"detail": APPROVED_NOT_EDITABLE}
    assert stored(engine, order_id).status == OrderStatus.approved


def test_version_starts_at_one_and_moves_with_each_write(client, engine, auth_headers):
    headers = auth_headers()
    order_id = create_order(client, headers)["id"]
    assert stored(engine, order_id).version == 1

    client.patch(f"/v1/orders/{order_id}", json=PATCH, headers=headers)

    assert stored(engine, order_id).version == 2
