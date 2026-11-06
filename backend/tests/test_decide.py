from datetime import date

import pytest
from pydantic import ValidationError

from app.decide import decide, failed_decision
from app.extraction_types import (
    FIELD_NAMES,
    Evidenced,
    ExtractionResult,
    FlagCode,
    Issue,
    Verification,
)
from app.models import OrderStatus

TODAY = date(2026, 11, 6)
PAGE = "Patient: Jane Doe\nDOB: 02/17/1961\nPrescriber: Dana Smith MD\nNPI: 1234567893"
PAGES = [PAGE]
TEXT_PAGES = {1}
SCAN_TEXT_PAGES: set[int] = set()


def field(value, quote, page=1, issue=Issue.none):
    return Evidenced(value=value, source_quote=quote, page=page, issue=issue)


def result(multiple_patients=False, embedded_instructions_found=False, **overrides):
    fields = {
        "patient_first_name": field("Jane", "Patient: Jane Doe"),
        "patient_last_name": field("Doe", "Patient: Jane Doe"),
        "patient_dob": field("02/17/1961", "DOB: 02/17/1961"),
        "provider_name": field("Dana Smith MD", "Prescriber: Dana Smith MD"),
        "provider_npi": field("1234567893", "NPI: 1234567893"),
    }
    return ExtractionResult(
        **fields | overrides,
        multiple_patients=multiple_patients,
        embedded_instructions_found=embedded_instructions_found,
    )


def run(extraction=None, pages=PAGES, text_pages=TEXT_PAGES):
    return decide(extraction or result(), pages, text_pages, TODAY)


def codes(decision, name):
    return [flag.code for flag in decision.flags if flag.field == name]


def evidence(decision, name):
    return next(item for item in decision.evidence if item.field == name)


def test_all_verified_and_valid_is_approved():
    decision = run()
    assert decision.status == OrderStatus.approved
    assert decision.flags == []
    assert decision.values == {
        "patient_first_name": "Jane",
        "patient_last_name": "Doe",
        "patient_dob": date(1961, 2, 17),
        "provider_name": "Dana Smith MD",
        "provider_npi": "1234567893",
    }
    assert [item.field for item in decision.evidence] == list(FIELD_NAMES)
    assert all(item.verification == Verification.verified for item in decision.evidence)


def test_evidence_keeps_quote_and_page():
    item = evidence(run(), "provider_npi")
    assert item.extracted_value == "1234567893"
    assert item.source_quote == "NPI: 1234567893"
    assert item.page == 1


def test_scan_never_approves():
    decision = run(text_pages=SCAN_TEXT_PAGES)
    assert decision.status == OrderStatus.needs_review
    assert [flag.code for flag in decision.flags] == [FlagCode.unverified_quote] * 5
    assert {flag.field for flag in decision.flags} == set(FIELD_NAMES)


def test_failed_quote_adds_quote_not_found_and_keeps_the_value():
    decision = run(result(patient_first_name=field("Janet", "Patient: Jane Doe")))
    assert codes(decision, "patient_first_name") == [FlagCode.quote_not_found]
    assert decision.values["patient_first_name"] == "Janet"
    assert decision.status == OrderStatus.needs_review


@pytest.mark.parametrize("printed", ["9182734556", "9182 734 556"])
def test_bad_npi_is_kept_verbatim_and_flagged(printed):
    pages = [PAGE + f"\nNPI: {printed}"]
    decision = run(result(provider_npi=field(printed, f"NPI: {printed}")), pages)
    assert codes(decision, "provider_npi") == [FlagCode.invalid_npi]
    assert decision.values["provider_npi"] == printed
    assert evidence(decision, "provider_npi").extracted_value == printed
    assert decision.status == OrderStatus.needs_review


def test_good_npi_with_spaces_stays_as_printed():
    pages = ["NPI: 1234 567 893\n" + PAGE]
    decision = run(result(provider_npi=field("1234 567 893", "NPI: 1234 567 893")), pages)
    assert decision.values["provider_npi"] == "1234 567 893"
    assert decision.status == OrderStatus.approved


def test_unparseable_dob_stays_in_evidence_only():
    pages = ["DOB: smudged\n" + PAGE]
    decision = run(result(patient_dob=field("smudged", "DOB: smudged")), pages)
    assert decision.values["patient_dob"] is None
    assert evidence(decision, "patient_dob").extracted_value == "smudged"
    assert codes(decision, "patient_dob") == [FlagCode.invalid_date]
    assert decision.status == OrderStatus.needs_review


def test_sample_dob_parses_and_is_flagged():
    pages = ["DOB: 12/05/1900\n" + PAGE]
    decision = run(result(patient_dob=field("12/05/1900", "DOB: 12/05/1900")), pages)
    assert decision.values["patient_dob"] == date(1900, 12, 5)
    assert codes(decision, "patient_dob") == [FlagCode.implausible_dob]


def test_future_dob_is_kept_and_flagged():
    pages = ["DOB: 12/05/2030\n" + PAGE]
    decision = run(result(patient_dob=field("12/05/2030", "DOB: 12/05/2030")), pages)
    assert decision.values["patient_dob"] == date(2030, 12, 5)
    assert codes(decision, "patient_dob") == [FlagCode.implausible_dob]


@pytest.mark.parametrize("name", FIELD_NAMES)
def test_null_value_is_missing_and_not_applicable(name):
    decision = run(result(**{name: field(None, None, None)}))
    assert decision.values[name] is None
    assert codes(decision, name) == [FlagCode.missing]
    assert evidence(decision, name).verification == Verification.not_applicable
    assert decision.status == OrderStatus.needs_review


@pytest.mark.parametrize("printed", ["N/A", "n/a", "unknown", "none", "-", ""])
def test_placeholders_count_as_missing(printed):
    decision = run(result(patient_last_name=field(printed, "Last name: N/A")))
    assert decision.values["patient_last_name"] is None
    assert codes(decision, "patient_last_name") == [FlagCode.missing]
    assert evidence(decision, "patient_last_name").verification == Verification.not_applicable
    assert evidence(decision, "patient_last_name").extracted_value == printed


@pytest.mark.parametrize("name", ["provider_npi", "patient_dob"])
def test_placeholder_adds_no_format_flag(name):
    decision = run(result(**{name: field("N/A", None, None)}))
    assert codes(decision, name) == [FlagCode.missing]


@pytest.mark.parametrize(
    ("issue", "code"),
    [
        (Issue.illegible, FlagCode.illegible),
        (Issue.ambiguous, FlagCode.ambiguous),
        (Issue.conflicting, FlagCode.conflicting_values),
    ],
)
def test_issue_values_map_to_flags(issue, code):
    null = run(result(provider_name=field(None, None, None, issue)))
    assert codes(null, "provider_name") == [code]

    placeholder = run(result(provider_name=field("unknown", None, None, issue)))
    assert codes(placeholder, "provider_name") == [code]

    present = run(
        result(provider_name=field("Dana Smith MD", "Prescriber: Dana Smith MD", 1, issue))
    )
    assert codes(present, "provider_name") == [code]
    assert present.values["provider_name"] == "Dana Smith MD"
    assert present.status == OrderStatus.needs_review


def test_multiple_patients_and_embedded_instructions_flag():
    decision = run(result(multiple_patients=True, embedded_instructions_found=True))
    assert codes(decision, None) == [FlagCode.multiple_patients, FlagCode.embedded_instructions]
    assert decision.status == OrderStatus.needs_review


FLAG_CASES = {
    FlagCode.missing: lambda: run(result(provider_name=field(None, None, None))),
    FlagCode.illegible: lambda: run(result(provider_name=field(None, None, None, Issue.illegible))),
    FlagCode.ambiguous: lambda: run(
        result(provider_name=field("Dana Smith MD", "Dana Smith MD", 1, Issue.ambiguous))
    ),
    FlagCode.conflicting_values: lambda: run(
        result(provider_name=field("Dana Smith MD", "Dana Smith MD", 1, Issue.conflicting))
    ),
    FlagCode.invalid_npi: lambda: run(
        result(provider_npi=field("9182734556", "9182734556")), ["9182734556\n" + PAGE]
    ),
    FlagCode.invalid_date: lambda: run(
        result(patient_dob=field("soon", "soon")), ["soon\n" + PAGE]
    ),
    FlagCode.implausible_dob: lambda: run(
        result(patient_dob=field("12/05/1900", "12/05/1900")), ["12/05/1900\n" + PAGE]
    ),
    FlagCode.unverified_quote: lambda: run(text_pages=SCAN_TEXT_PAGES),
    FlagCode.quote_not_found: lambda: run(result(provider_name=field("Dana", "Elsewhere"))),
    FlagCode.multiple_patients: lambda: run(result(multiple_patients=True)),
    FlagCode.embedded_instructions: lambda: run(result(embedded_instructions_found=True)),
    FlagCode.extraction_failed: failed_decision,
}


@pytest.mark.parametrize("code", list(FlagCode))
def test_any_flag_blocks_approval(code):
    decision = FLAG_CASES[code]()
    assert code in [flag.code for flag in decision.flags]
    assert decision.status == OrderStatus.needs_review


@pytest.mark.parametrize("case", FLAG_CASES.values(), ids=[str(code) for code in FLAG_CASES])
def test_decide_never_returns_denied(case):
    assert case().status != OrderStatus.denied


def test_decide_never_returns_denied_on_a_clean_result():
    assert run().status != OrderStatus.denied


def test_failed_decision_is_empty_and_flagged():
    decision = failed_decision()
    assert decision.status == OrderStatus.needs_review
    assert [(flag.field, flag.code) for flag in decision.flags] == [
        (None, FlagCode.extraction_failed)
    ]
    assert decision.values == dict.fromkeys(FIELD_NAMES)
    assert decision.evidence == []


def test_model_schema_has_no_status_or_free_text():
    assert set(ExtractionResult.model_fields) == {
        *FIELD_NAMES,
        "multiple_patients",
        "embedded_instructions_found",
    }
    assert set(Evidenced.model_fields) == {"value", "source_quote", "page", "issue"}
    with pytest.raises(ValidationError):
        ExtractionResult(**result().model_dump(), status="approved")


def test_flag_codes_and_verifications_match_the_spec():
    assert {code.value for code in FlagCode} == {
        "missing",
        "illegible",
        "ambiguous",
        "conflicting_values",
        "invalid_npi",
        "invalid_date",
        "implausible_dob",
        "unverified_quote",
        "quote_not_found",
        "multiple_patients",
        "embedded_instructions",
        "extraction_failed",
    }
    assert {item.value for item in Verification} == {
        "verified",
        "unverified",
        "failed",
        "not_applicable",
    }
