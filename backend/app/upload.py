"""POST /v1/orders/upload: PDF in, Order out.

Checks run as dependencies, because FastAPI parses a declared `UploadFile` before any dependency
runs, which would spool a whole body from an unauthenticated caller to disk. Here the chain is
auth, then quota, then a bounded read, and read_pdf takes it from there. The route stays sync, so
pdfplumber and the Claude call run in the threadpool.
"""

import hashlib
import logging
import threading
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlmodel import Session, select
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect

from app.audit import audit
from app.auth import Auth, Db, Principal
from app.body_limit import TOO_LARGE, upload_body_limit
from app.config import settings
from app.decide import Decision, decide, failed_decision
from app.llm import extract, get_llm_client
from app.models import Document, FieldEvidence, Order, OrderSource, ReviewFlag, utcnow
from app.orders import _audited_out
from app.pdf_intake import PdfRejected, read_pdf
from app.schemas import OrderOut

router = APIRouter(prefix="/v1/orders", tags=["orders"])
log = logging.getLogger("app.upload")

MAX_CONCURRENT_EXTRACTIONS = 8  # a PDF worker and a model call each, out of 40 threadpool threads
NO_FILE = "Send one PDF in the multipart field `file`."

BUSY = "Too many uploads are in progress. Try again shortly."
QUOTA_REACHED = "Daily upload limit reached."

# Held from the count through the commit, so two uploads can't both take the last place. It covers
# one process, which is how the Dockerfile runs this.
_quota_lock = threading.Lock()
# Taken before anything is stored, so a refused upload loses nothing and spends no quota.
extraction_slots = threading.BoundedSemaphore(MAX_CONCURRENT_EXTRACTIONS)

# The route reads its own body, so FastAPI can't work this out for Swagger's file picker.
UPLOAD_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {"file": {"type": "string", "format": "binary"}},
                }
            }
        },
    }
}


class _BodyTooLarge(Exception):
    pass


def _over_quota(db: Session, user_id: str) -> bool:
    """Counts stored Documents, so a rejected file costs nothing and a failed extraction does."""
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today = select(func.count()).select_from(Document).where(Document.created_at >= day_start)
    everyone = db.exec(today).one()
    yours = db.exec(today.where(Document.owner_id == user_id)).one()
    db.rollback()  # no transaction stays open while a slow client sends its body
    return yours >= settings.quota_user_daily or everyone >= settings.quota_global_daily


def enforce_quota(who: Auth, db: Db) -> None:
    if _over_quota(db, who.user_id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=QUOTA_REACHED)


Quota = Annotated[None, Depends(enforce_quota)]


async def read_upload(request: Request, _: Quota) -> bytes:
    """The bytes of field `file`, at most upload_max_bytes + 1, so read_pdf can tell it is over.

    The filename and part content type are never read: neither is trusted, and a filename can hold
    a patient's name.
    """
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=NO_FILE)

    limit = upload_body_limit()

    async def bounded_body():
        seen = 0
        async for chunk in request.stream():
            seen += len(chunk)
            if seen > limit:
                raise _BodyTooLarge
            yield chunk

    parser = MultiPartParser(request.headers, bounded_body(), max_files=1, max_fields=1)
    try:
        form = await parser.parse()
    except _BodyTooLarge:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail=TOO_LARGE) from None
    except (MultiPartException, ClientDisconnect):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=NO_FILE) from None
    try:
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=NO_FILE)
        return await file.read(settings.upload_max_bytes + 1)
    finally:
        await form.close()


Upload = Annotated[bytes, Depends(read_upload)]
LlmClient = Annotated[Any, Depends(get_llm_client)]


def _store_document(db: Session, who: Principal, data: bytes, page_count: int, pages: set[int]):
    """Committed before the model is called, so a failed call can't lose the fax.

    The early check ran before the body arrived, and any number of uploads can pass it together.
    This one decides.
    """
    with _quota_lock:
        if _over_quota(db, who.user_id):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=QUOTA_REACHED)
        document = Document(
            owner_id=who.user_id,
            sha256=hashlib.sha256(data).hexdigest(),
            byte_size=len(data),
            page_count=page_count,
            text_pages=sorted(pages),
            content=data,
        )
        document_id = document.id  # read now: commit expires it, and touching it reopens a read
        db.add(document)
        audit(db, who.user_id, "document.upload", "document", document_id)
        db.commit()
    return document_id


def _save_order(
    db: Session, who: Principal, document_id: str, decision: Decision, page_count: int
) -> OrderOut:
    order = Order(
        owner_id=who.user_id,
        source=OrderSource.upload,
        status=decision.status,
        document_id=document_id,
        **decision.values,
    )
    db.add(order)
    db.flush()  # the rows below have foreign keys to it
    for item in decision.evidence:
        # A page outside the PDF is no page at all, and past 2**63 SQLite can't store it.
        real_page = item.page is not None and 1 <= item.page <= page_count
        db.add(
            FieldEvidence(
                owner_id=who.user_id,
                order_id=order.id,
                field=item.field,
                extracted_value=item.extracted_value,
                source_quote=item.source_quote,
                page=item.page if real_page else None,
                verification=item.verification,
            )
        )
    for flag in decision.flags:
        db.add(
            ReviewFlag(owner_id=who.user_id, order_id=order.id, field=flag.field, code=flag.code)
        )
    return _audited_out(db, who, order, "order.create")


def _save_with_retry(
    db: Session, who: Principal, document_id: str, decision: Decision, page_count: int
) -> OrderOut:
    """The fax is safe and the model has been paid for, so one lost write gets a second try."""
    try:
        return _save_order(db, who, document_id, decision, page_count)
    except Exception as error:
        db.rollback()
        log.warning("order_save_failed", extra={"reason": type(error).__name__})
    try:
        return _save_order(db, who, document_id, decision, page_count)
    except Exception as error:
        db.rollback()
        log.warning("order_save_failed", extra={"reason": type(error).__name__})
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"The file was saved as document {document_id}, but its order was not.",
        ) from None


def _process(who: Principal, db: Session, data: bytes, client: Any) -> OrderOut:
    try:
        parsed = read_pdf(data)
    except PdfRejected as rejected:
        raise HTTPException(rejected.status_code, detail=rejected.detail) from None

    document_id = _store_document(db, who, data, parsed.page_count, parsed.text_pages)

    # No transaction is open here, so a slow call holds no lock and other users can still write.
    try:
        result = extract(data, parsed.pages, parsed.text_pages, client)
        decision = decide(result, parsed.pages, parsed.text_pages, utcnow().date())
    except Exception as error:
        # The Document is committed, so every failure from here on must still end in an Order.
        # Only the type: a message can quote the document.
        log.warning("extraction_failed", extra={"reason": type(error).__name__})
        decision = failed_decision()

    return _save_with_retry(db, who, document_id, decision, parsed.page_count)


@router.post("/upload", status_code=status.HTTP_201_CREATED, openapi_extra=UPLOAD_BODY)
def upload_order(who: Auth, db: Db, data: Upload, client: LlmClient) -> OrderOut:
    if not extraction_slots.acquire(blocking=False):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=BUSY, headers={"Retry-After": "30"}
        )
    try:
        return _process(who, db, data, client)
    finally:
        extraction_slots.release()
