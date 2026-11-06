import re
from datetime import UTC, date, datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, StringConstraints

from app.models import OrderSource, OrderStatus, utcnow
from app.validators import check_dob, is_valid_npi

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _as_utc(value: datetime) -> datetime:
    # SQLite drops the timezone. Every stored timestamp is UTC, so say so to the client.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]


# A person can fix a typo on the spot, unlike a fax, so these refuse rather than flag. The messages
# name no value: the 422 handler hides them anyway, but they could reach a log.
def _valid_npi(value: str) -> str:
    if not is_valid_npi(value):
        raise ValueError("Not a valid NPI.")
    return value


def _date_string(value: Any) -> Any:
    # Pydantic alone reads numbers, and numeric strings, as timestamps: 0 becomes 1970-01-01.
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Must be a YYYY-MM-DD string.")
    return value


def _plausible_dob(value: date) -> date:
    if check_dob(value, utcnow().date()) is not None:
        raise ValueError("Not a plausible date of birth.")
    return value


Npi = Annotated[Text, AfterValidator(_valid_npi)]
Dob = Annotated[date, BeforeValidator(_date_string), AfterValidator(_plausible_dob)]


class OrderIn(BaseModel):
    """extra="forbid" is what stops a client setting status or owner."""

    model_config = ConfigDict(extra="forbid")

    patient_first_name: Text
    patient_last_name: Text
    patient_dob: Dob
    provider_name: Text
    provider_npi: Npi


class OrderPatch(BaseModel):
    """Omit a field to leave it alone. Null is refused: the types below are deliberately not
    Optional, because an edit may correct a value but not erase it. Defaults aren't validated."""

    model_config = ConfigDict(extra="forbid")

    patient_first_name: Text = None
    patient_last_name: Text = None
    patient_dob: Dob = None
    provider_name: Text = None
    provider_npi: Npi = None


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
