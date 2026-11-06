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
from tests.test_validators import PRINTED_PLACEHOLDERS, REAL_NAMES

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


def on_second_page(name, value, quote, printed, issue=Issue.none):
    """Every other field stays clean, so this field alone decides the outcome."""
    return run(result(**{name: field(value, quote, 2, issue)}), [PAGE, printed], {1, 2})


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


@pytest.mark.parametrize(
    ("name", "value", "printed"),
    [
        ("patient_dob", "2/17/1961", "DOB: 12/17/1961"),
        ("patient_first_name", "Jan", "Name: Janet"),
        ("patient_last_name", "Smith", "Name: Smithson"),
        ("provider_npi", "1234567893", "NPI: 91234567893"),
    ],
)
def test_truncated_value_and_quote_are_not_approved(name, value, printed):
    decision = on_second_page(name, value, value, printed)
    assert decision.status == OrderStatus.needs_review
    assert codes(decision, name) == [FlagCode.quote_not_found]


@pytest.mark.parametrize(
    ("name", "value", "quote"),
    [
        ("provider_npi", "1234567893", "Member ID 991234567893"),
        ("provider_npi", "1234567893", "Member ID 12345678931"),
        ("provider_npi", "1234567893", "Zip 12345 Ext 67893"),
        ("patient_first_name", "1205198011", "12/05/1980 11"),
    ],
)
def test_digits_lifted_from_other_numbers_are_not_approved(name, value, quote):
    decision = on_second_page(name, value, quote, quote)
    assert decision.status == OrderStatus.needs_review
    assert FlagCode.quote_not_found in codes(decision, name)


@pytest.mark.parametrize("quote", ["NPI: 1234 567 893", "NPI: 1234-567-893"])
def test_spaced_npi_quote_still_approves_a_plain_value(quote):
    decision = on_second_page("provider_npi", "1234567893", quote, quote)
    assert decision.status == OrderStatus.approved


@pytest.mark.parametrize("name", ["patient_first_name", "patient_last_name", "provider_name"])
@pytest.mark.parametrize("value", PRINTED_PLACEHOLDERS)
def test_printed_placeholder_names_are_not_approved(name, value):
    decision = on_second_page(name, value, value, f"Name: {value}")
    assert decision.status == OrderStatus.needs_review
    assert decision.values[name] is None
    assert codes(decision, name) == [FlagCode.missing]
    assert evidence(decision, name).extracted_value == value


@pytest.mark.parametrize("value", REAL_NAMES)
def test_real_names_still_approve(value):
    decision = on_second_page("patient_last_name", value, value, f"Name: {value}")
    assert decision.status == OrderStatus.approved


@pytest.mark.parametrize(
    ("name", "value", "printed"),
    [
        ("patient_last_name", "Smith", "Name: Smith-Jones"),
        ("patient_last_name", "Brien", "Name: O'Brien"),
        ("patient_first_name", "Jan", "Name: Jan\u200be"),
        ("patient_last_name", "Smi", "Name: Smi\u00adthson"),
        ("patient_dob", "2/17/1961", "DOB: 1\u200b2/17/1961"),
        ("patient_first_name", "Jan", "Name: Jan\u0308e"),
    ],
)
@pytest.mark.parametrize("quote_whole_word", [True, False])
def test_part_of_a_joined_word_is_not_approved(name, value, printed, quote_whole_word):
    quote = printed.split(": ")[1] if quote_whole_word else value
    decision = on_second_page(name, value, quote, printed)
    assert decision.status == OrderStatus.needs_review
    assert codes(decision, name) == [FlagCode.quote_not_found]


def test_full_hyphenated_name_still_approves():
    decision = on_second_page("patient_last_name", "Smith-Jones", "Smith-Jones", "Smith-Jones")
    assert decision.status == OrderStatus.approved


@pytest.mark.parametrize(
    ("printed", "expected"),
    [("02/17/61", date(1961, 2, 17)), ("11/07/26", date(1926, 11, 7))],
)
def test_two_digit_year_is_flagged_ambiguous_and_keeps_its_value(printed, expected):
    decision = on_second_page("patient_dob", printed, f"DOB: {printed}", f"DOB: {printed}")
    assert decision.status == OrderStatus.needs_review
    assert codes(decision, "patient_dob") == [FlagCode.ambiguous]
    assert decision.values["patient_dob"] == expected
    assert evidence(decision, "patient_dob").extracted_value == printed


def test_two_digit_year_with_ambiguous_issue_flags_once():
    decision = on_second_page("patient_dob", "02/17/61", "02/17/61", "02/17/61", Issue.ambiguous)
    assert codes(decision, "patient_dob") == [FlagCode.ambiguous]


@pytest.mark.parametrize("printed", ["05/12/1980", "12/05/1980", "1980-12-05", "December 5, 1980"])
def test_four_digit_years_are_read_without_an_ambiguity_flag(printed):
    decision = on_second_page("patient_dob", printed, f"DOB: {printed}", f"DOB: {printed}")
    assert decision.status == OrderStatus.approved


@pytest.mark.parametrize("npi", ["3000000000", "9999999995"])
def test_npi_passing_luhn_with_a_bad_first_digit_is_not_approved(npi):
    decision = on_second_page("provider_npi", npi, npi, f"NPI: {npi}")
    assert decision.status == OrderStatus.needs_review
    assert codes(decision, "provider_npi") == [FlagCode.invalid_npi]


@pytest.mark.parametrize("name", ["patient_first_name", "patient_last_name", "provider_name"])
@pytest.mark.parametrize("value", ["5551234567", "12/05/1980", "1234"])
def test_name_without_letters_is_missing(name, value):
    decision = on_second_page(name, value, f"Phone {value}", f"Phone {value}")
    assert decision.status == OrderStatus.needs_review
    assert decision.values[name] is None
    assert codes(decision, name) == [FlagCode.missing]
    assert evidence(decision, name).extracted_value == value


def test_name_without_letters_keeps_its_issue_flag():
    decision = on_second_page(
        "patient_last_name", "1234", "Phone 1234", "Phone 1234", Issue.illegible
    )
    assert codes(decision, "patient_last_name") == [FlagCode.illegible]


@pytest.mark.parametrize(
    ("name", "printed", "stored"),
    [
        ("patient_first_name", "  Jane\n", "Jane"),
        ("provider_name", "\tDana Smith MD ", "Dana Smith MD"),
        ("provider_npi", " 1234567893\n", "1234567893"),
    ],
)
def test_outer_whitespace_leaves_the_order_value_but_not_the_evidence(name, printed, stored):
    decision = on_second_page(name, printed, stored, f"Printed: {stored}")
    assert decision.status == OrderStatus.approved
    assert decision.values[name] == stored
    assert evidence(decision, name).extracted_value == printed


def test_unparseable_dob_keeps_its_issue_flag():
    decision = on_second_page("patient_dob", "soon", "soon", "soon", Issue.illegible)
    assert codes(decision, "patient_dob") == [FlagCode.illegible, FlagCode.invalid_date]


def test_implausible_dob_keeps_its_issue_flag():
    decision = on_second_page(
        "patient_dob", "12/05/1900", "12/05/1900", "12/05/1900", Issue.conflicting
    )
    assert codes(decision, "patient_dob") == [
        FlagCode.conflicting_values,
        FlagCode.implausible_dob,
    ]


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
