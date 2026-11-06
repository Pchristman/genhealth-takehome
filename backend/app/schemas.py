from datetime import UTC, date, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

from app.models import OrderSource, OrderStatus

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _as_utc(value: datetime) -> datetime:
    # SQLite drops the timezone. Every stored timestamp is UTC, so say so to the client.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]


class OrderIn(BaseModel):
    """extra="forbid" is what stops a client setting status or owner."""

    model_config = ConfigDict(extra="forbid")

    patient_first_name: Text
    patient_last_name: Text
    patient_dob: date
    provider_name: Text
    provider_npi: Text


class OrderPatch(BaseModel):
    """Omit a field to leave it alone. Null is refused: the types below are deliberately not
    Optional, because an edit may correct a value but not erase it. Defaults aren't validated."""

    model_config = ConfigDict(extra="forbid")

    patient_first_name: Text = None
    patient_last_name: Text = None
    patient_dob: date = None
    provider_name: Text = None
    provider_npi: Text = None


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    field: str
    extracted_value: str | None
    source_quote: str | None
    page: int | None
    verification: str
    corrected: bool


class FlagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    field: str | None
    code: str
    resolved_by: str | None
    resolved_at: UtcDatetime | None


class OrderOut(BaseModel):
    id: str
    status: OrderStatus
    source: OrderSource
    patient_first_name: str | None
    patient_last_name: str | None
    patient_dob: date | None
    provider_name: str | None
    provider_npi: str | None
    document_id: str | None
    reviewed_by: str | None
    reviewed_at: UtcDatetime | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    evidence: list[EvidenceOut]
    flags: list[FlagOut]


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int
