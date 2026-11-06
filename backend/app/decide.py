"""The status gate. Code approves or escalates here; only a person can deny.

Flags are added, values are never repaired: what the document printed stays visible for review.
"""

from dataclasses import dataclass
from datetime import date

from app.extraction_types import (
    FIELD_NAMES,
    Evidenced,
    ExtractionResult,
    FlagCode,
    Issue,
    Verification,
)
from app.grounding import verify_quote
from app.models import OrderStatus
from app.validators import (
    check_dob,
    has_two_digit_year,
    is_placeholder,
    is_valid_npi,
    parse_dob,
)

ISSUE_FLAGS = {
    Issue.illegible: FlagCode.illegible,
    Issue.ambiguous: FlagCode.ambiguous,
    Issue.conflicting: FlagCode.conflicting_values,
}
VERIFICATION_FLAGS = {
    Verification.failed: FlagCode.quote_not_found,
    Verification.unverified: FlagCode.unverified_quote,
}
NAME_FIELDS = frozenset({"patient_first_name", "patient_last_name", "provider_name"})


@dataclass(frozen=True)
class Flag:
    field: str | None
    code: FlagCode


@dataclass(frozen=True)
class EvidenceItem:
    field: str
    extracted_value: str | None
    source_quote: str | None
    page: int | None
    verification: Verification


@dataclass(frozen=True)
class Decision:
    status: OrderStatus
    flags: list[Flag]
    evidence: list[EvidenceItem]
    values: dict[str, str | date | None]  # keyed by Order column


def _value_flags(
    name: str, field: Evidenced, today: date
) -> tuple[str | date | None, list[FlagCode]]:
    """The Order's value for a field, plus what is wrong with it."""
    issue_flag = ISSUE_FLAGS.get(field.issue)
    codes = [issue_flag] if issue_flag else []
    no_letters = name in NAME_FIELDS and not any(c.isalpha() for c in field.value or "")
    if no_letters or is_placeholder(field.value):
        return None, codes or [FlagCode.missing]
    if name == "provider_npi" and not is_valid_npi(field.value):
        codes.append(FlagCode.invalid_npi)
    if name != "patient_dob":
        return field.value.strip(), codes
    parsed = parse_dob(field.value, today)
    if parsed is None:
        return None, [*codes, FlagCode.invalid_date]
    if has_two_digit_year(field.value) and FlagCode.ambiguous not in codes:
        codes.append(FlagCode.ambiguous)  # the century is a guess
    dob_flag = check_dob(parsed, today)
    return parsed, [*codes, dob_flag] if dob_flag else codes


def decide(
    result: ExtractionResult, pages: list[str], text_pages: set[int], today: date
) -> Decision:
    flags: list[Flag] = []
    evidence: list[EvidenceItem] = []
    values: dict[str, str | date | None] = {}

    for name in FIELD_NAMES:
        field: Evidenced = getattr(result, name)
        verification = verify_quote(field, pages, text_pages, name)
        values[name], codes = _value_flags(name, field, today)
        if verification in VERIFICATION_FLAGS:
            codes = [*codes, VERIFICATION_FLAGS[verification]]
        flags.extend(Flag(name, code) for code in codes)
        evidence.append(
            EvidenceItem(name, field.value, field.source_quote, field.page, verification)
        )

    if result.multiple_patients:
        flags.append(Flag(None, FlagCode.multiple_patients))
    if result.embedded_instructions_found:
        flags.append(Flag(None, FlagCode.embedded_instructions))

    gate_passes = (
        not flags
        and all(value is not None for value in values.values())
        and all(item.verification == Verification.verified for item in evidence)
    )
    status = OrderStatus.approved if gate_passes else OrderStatus.needs_review
    return Decision(status, flags, evidence, values)


def failed_decision() -> Decision:
    """The model gave nothing usable. The Order is still saved, so the fax isn't lost."""
    return Decision(
        status=OrderStatus.needs_review,
        flags=[Flag(None, FlagCode.extraction_failed)],
        evidence=[],
        values=dict.fromkeys(FIELD_NAMES),
    )
