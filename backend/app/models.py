"""Tables. Everything a user owns carries owner_id, and that is the only scope."""

from datetime import UTC, date, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class OrderStatus(StrEnum):
    needs_review = "needs_review"
    approved = "approved"
    denied = "denied"


class OrderSource(StrEnum):
    upload = "upload"
    manual = "manual"


class User(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    email: str = Field(unique=True, index=True)  # stored lowercase
    password_hash: str
    created_at: datetime = Field(default_factory=utcnow)


class Document(SQLModel, table=True):
    """The source PDF. There is no filename column: filenames are never stored."""

    id: str = Field(default_factory=new_id, primary_key=True)
    owner_id: str = Field(foreign_key="user.id", index=True)
    sha256: str
    byte_size: int
    page_count: int
    text_pages: list[int] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    content: bytes | None = None  # cleared when the Order is deleted
    created_at: datetime = Field(default_factory=utcnow)


class Order(SQLModel, table=True):
    """Load one only through orders.get_order. Fields are nullable because a fax can lack them."""

    id: str = Field(default_factory=new_id, primary_key=True)
    owner_id: str = Field(foreign_key="user.id", index=True)
    status: OrderStatus = OrderStatus.needs_review
    source: OrderSource
    patient_first_name: str | None = None
    patient_last_name: str | None = None
    patient_dob: date | None = None
    provider_name: str | None = None
    provider_npi: str | None = None
    document_id: str | None = Field(default=None, foreign_key="document.id")
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class FieldEvidence(SQLModel, table=True):
    """What the source said. Never edited apart from `corrected`."""

    id: str = Field(default_factory=new_id, primary_key=True)
    owner_id: str = Field(foreign_key="user.id", index=True)
    order_id: str = Field(foreign_key="order.id", index=True)
    field: str
    extracted_value: str | None = None
    source_quote: str | None = None
    page: int | None = None
    verification: str
    corrected: bool = False


class ReviewFlag(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    owner_id: str = Field(foreign_key="user.id", index=True)
    order_id: str = Field(foreign_key="order.id", index=True)
    field: str | None = None
    code: str
    resolved_by: str | None = None
    resolved_at: datetime | None = None


class AuditEvent(SQLModel, table=True):
    """Append-only record of who touched what. Ids and action names, never patient content."""

    id: str = Field(default_factory=new_id, primary_key=True)
    actor_id: str = Field(index=True)
    action: str
    resource_type: str
    resource_id: str
    request_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
