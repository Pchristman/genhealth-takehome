"""Tables. Everything a user owns carries owner_id, and that is the only scope."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class User(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    email: str = Field(unique=True, index=True)  # stored lowercase
    password_hash: str
    created_at: datetime = Field(default_factory=utcnow)


class AuditEvent(SQLModel, table=True):
    """Append-only record of who touched what. Ids and action names, never patient content."""

    id: str = Field(default_factory=new_id, primary_key=True)
    actor_id: str = Field(index=True)
    action: str
    resource_type: str
    resource_id: str
    request_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
