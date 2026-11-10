"""Order CRUD. Every route commits its audit row in the same transaction as its work."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy import func, inspect
from sqlmodel import Session, col, select

from app.audit import audit
from app.auth import Auth, Db, Principal
from app.models import (
    Document,
    FieldEvidence,
    Order,
    OrderSource,
    OrderStatus,
    ReviewFlag,
    utcnow,
)
from app.schemas import EvidenceOut, FlagOut, OrderIn, OrderOut, OrderPatch, Page

router = APIRouter(prefix="/v1/orders", tags=["orders"])

MAX_LIMIT = 100
MAX_OFFSET = 2**63 - 1  # SQLite's integer limit; past it the query would fail with a 500
APPROVED_NOT_EDITABLE = "An approved Order is final and can't be edited."
DENIED_NOT_EDITABLE = "A denied Order can't be edited."


def get_order(db: Session, who: Principal, order_id: str) -> Order:
    """The only single-Order loader. A miss commits its audit row and discards anything pending.

    Missing, deleted and someone else's all get the same 404, so the response can't confirm that
    an id exists. Pass a validated UUID: a miss stores the id in the audit table.
    """
    statement = select(Order).where(
        Order.id == order_id,
        Order.owner_id == who.user_id,
        col(Order.deleted_at).is_(None),
    )
    order = db.exec(statement).first()
    if order is None:
        db.rollback()  # the commit below is for this audit row, not for the caller's pending rows
        audit(db, who.user_id, "order.access_denied", "order", order_id)
        db.commit()
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Order not found.")
    return order


def _to_out(db: Session, who: Principal, orders: list[Order]) -> list[OrderOut]:
    ids = [order.id for order in orders]
    evidence = db.exec(
        select(FieldEvidence)
        .where(FieldEvidence.owner_id == who.user_id, col(FieldEvidence.order_id).in_(ids))
        .order_by(col(FieldEvidence.id))
    ).all()
    flags = db.exec(
        select(ReviewFlag)
        .where(ReviewFlag.owner_id == who.user_id, col(ReviewFlag.order_id).in_(ids))
        .order_by(col(ReviewFlag.id))
    ).all()
    linked = [order.document_id for order in orders if order.document_id]
    own_documents = set(
        db.exec(
            select(Document.id).where(
                Document.owner_id == who.user_id, col(Document.id).in_(linked)
            )
        )
    )
    return [
        OrderOut(
            **order.model_dump(exclude={"owner_id", "deleted_at", "document_id"}),
            document_id=order.document_id if order.document_id in own_documents else None,
            evidence=[
                EvidenceOut.model_validate(row) for row in evidence if row.order_id == order.id
            ],
            flags=[FlagOut.model_validate(row) for row in flags if row.order_id == order.id],
        )
        for order in orders
    ]


def _audited_out(db: Session, who: Principal, order: Order, action: str) -> OrderOut:
    """Record the action and return the Order. If the audit can't commit, nothing is returned."""
    audit(db, who.user_id, action, "order", order.id)
    out = _to_out(db, who, [order])[0]
    db.commit()
    return out


@router.get("")
def list_orders(
    who: Auth,
    db: Db,
    status: OrderStatus | None = None,
    limit: Annotated[int, Query(ge=1)] = 50,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
) -> Page[OrderOut]:
    limit = min(limit, MAX_LIMIT)
    scope = [Order.owner_id == who.user_id, col(Order.deleted_at).is_(None)]
    if status is not None:
        scope.append(Order.status == status)
    total = db.exec(select(func.count()).select_from(Order).where(*scope)).one()
    orders = db.exec(
        select(Order)
        .where(*scope)
        .order_by(col(Order.created_at).desc(), col(Order.id).desc())
        .limit(limit)
        .offset(offset)
    ).all()
    items = _to_out(db, who, list(orders))
    for order in orders:
        audit(db, who.user_id, "order.read", "order", order.id)
    db.commit()
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("", status_code=http_status.HTTP_201_CREATED)
def create_order(body: OrderIn, who: Auth, db: Db) -> OrderOut:
    order = Order(owner_id=who.user_id, source=OrderSource.manual, **body.model_dump())
    db.add(order)
    return _audited_out(db, who, order, "order.create")


@router.get("/{order_id}")
def read_order(order_id: UUID, who: Auth, db: Db) -> OrderOut:
    order = get_order(db, who, str(order_id))
    return _audited_out(db, who, order, "order.read")


def _record_corrections(db: Session, who: Principal, order: Order, fields: set[str]) -> None:
    """A person's edit settles that field's flags.

    Call it after the last change to the Order. Its queries flush, which ends the history it reads
    and writes the Order: a change made after that would be a second UPDATE.
    """
    state = inspect(order).attrs
    changed = [field for field in fields if state[field].history.has_changes()]
    now = utcnow()
    for flag in db.exec(
        select(ReviewFlag).where(
            ReviewFlag.owner_id == who.user_id,
            ReviewFlag.order_id == order.id,
            col(ReviewFlag.field).in_(changed),
            col(ReviewFlag.resolved_at).is_(None),
        )
    ):
        flag.resolved_by = who.user_id
        flag.resolved_at = now
        db.add(flag)
    for row in db.exec(
        select(FieldEvidence).where(
            FieldEvidence.owner_id == who.user_id,
            FieldEvidence.order_id == order.id,
            col(FieldEvidence.field).in_(changed),
        )
    ):
        row.corrected = True
        db.add(row)


@router.patch("/{order_id}")
def update_order(order_id: UUID, body: OrderPatch, who: Auth, db: Db) -> OrderOut:
    order = get_order(db, who, str(order_id))
    if order.status == OrderStatus.approved:
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail=APPROVED_NOT_EDITABLE)
    if order.status == OrderStatus.denied:
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail=DENIED_NOT_EDITABLE)
    changes = {
        field: value
        for field, value in body.model_dump(exclude_unset=True).items()
        if getattr(order, field) != value
    }
    if not changes:
        return _audited_out(db, who, order, "order.read")
    for field, value in changes.items():
        setattr(order, field, value)
    order.updated_at = utcnow()
    db.add(order)
    _record_corrections(db, who, order, body.model_fields_set)
    return _audited_out(db, who, order, "order.update")


@router.delete("/{order_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_order(order_id: UUID, who: Auth, db: Db) -> None:
    order = get_order(db, who, str(order_id))
    now = utcnow()
    order.deleted_at = now
    order.updated_at = now
    db.add(order)
    if order.document_id is not None:
        document = db.exec(
            select(Document).where(
                Document.id == order.document_id, Document.owner_id == who.user_id
            )
        ).first()
        if document is not None:
            document.content = None
            db.add(document)
    audit(db, who.user_id, "order.delete", "order", order.id)
    db.commit()
