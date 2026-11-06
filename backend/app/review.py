"""A person's decision on an Order, and the source PDF they make it from."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from fastapi import status as http_status
from sqlalchemy import update
from sqlmodel import Session, col, select

from app.audit import audit
from app.auth import Auth, Db, Principal
from app.extraction_types import FIELD_NAMES
from app.models import Document, Order, OrderStatus, utcnow
from app.orders import _audited_out, get_order
from app.schemas import OrderOut, ReviewIn

router = APIRouter(prefix="/v1/orders", tags=["orders"])

NOT_REVIEWABLE = "Only an Order that needs review can be reviewed."
MISSING_FIELDS = "Every field must have a value before approval."
DOCUMENT_HEADERS = {
    "Content-Disposition": 'inline; filename="order.pdf"',
    "X-Content-Type-Options": "nosniff",
}
DECISIONS = {"approve": OrderStatus.approved, "deny": OrderStatus.denied}


def _claim_for_review(db: Session, order: Order) -> None:
    """Write only if the Order is still open: a read can't see a review committed after it.

    The write below goes through the ORM, so a version column is checked and bumped there too.
    """
    still_open = update(Order).where(
        col(Order.id) == order.id,
        col(Order.status) == OrderStatus.needs_review,
        col(Order.deleted_at).is_(None),
    )
    if db.exec(still_open.values(updated_at=utcnow())).rowcount != 1:
        db.rollback()
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail=NOT_REVIEWABLE)


@router.post("/{order_id}/review")
def review_order(order_id: UUID, body: ReviewIn, who: Auth, db: Db) -> OrderOut:
    order = get_order(db, who, str(order_id))
    if order.status != OrderStatus.needs_review:
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail=NOT_REVIEWABLE)
    if body.decision == "approve" and any(getattr(order, name) is None for name in FIELD_NAMES):
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail=MISSING_FIELDS)
    _claim_for_review(db, order)
    now = utcnow()
    order.status = DECISIONS[body.decision]
    order.reviewed_by = who.user_id
    order.reviewed_at = now
    order.updated_at = now
    db.add(order)
    return _audited_out(db, who, order, "order.review")


def _stored_pdf(db: Session, who: Principal, order: Order) -> tuple[str, bytes]:
    document = None
    if order.document_id is not None:
        document = db.exec(
            select(Document).where(
                Document.id == order.document_id, Document.owner_id == who.user_id
            )
        ).first()
    if document is None or document.content is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return document.id, document.content


@router.get("/{order_id}/document", response_class=Response)
def read_document(order_id: UUID, who: Auth, db: Db) -> Response:
    order = get_order(db, who, str(order_id))
    document_id, content = _stored_pdf(db, who, order)
    audit(db, who.user_id, "document.read", "document", document_id)
    db.commit()  # first: if the read can't be recorded, the bytes don't leave
    return Response(content, media_type="application/pdf", headers=DOCUMENT_HEADERS)
