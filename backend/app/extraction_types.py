"""The model's output schema and the vocabulary code judges it with.

Field names match the Order columns. There is no status, confidence or free-text field, so a
document that talks the model round has nothing to change.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class FlagCode(StrEnum):
    missing = "missing"
    illegible = "illegible"
    ambiguous = "ambiguous"
    conflicting_values = "conflicting_values"
    invalid_npi = "invalid_npi"
    invalid_date = "invalid_date"
    implausible_dob = "implausible_dob"
    unverified_quote = "unverified_quote"
    quote_not_found = "quote_not_found"
    multiple_patients = "multiple_patients"
    embedded_instructions = "embedded_instructions"
    extraction_failed = "extraction_failed"


class Verification(StrEnum):
    verified = "verified"
    unverified = "unverified"
    failed = "failed"
    not_applicable = "not_applicable"


class Issue(StrEnum):
    none = "none"
    illegible = "illegible"
    ambiguous = "ambiguous"
    conflicting = "conflicting"


class Evidenced(BaseModel):
    """One field as printed. `page` is the 1-based PDF page, not a printed fax page number."""

    model_config = ConfigDict(extra="forbid")

    value: str | None
    source_quote: str | None
    page: int | None
    issue: Issue


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_first_name: Evidenced
    patient_last_name: Evidenced
    patient_dob: Evidenced
    provider_name: Evidenced
    provider_npi: Evidenced
    multiple_patients: bool
    embedded_instructions_found: bool


FIELD_NAMES = (
    "patient_first_name",
    "patient_last_name",
    "patient_dob",
    "provider_name",
    "provider_npi",
)
