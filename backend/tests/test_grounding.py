import pytest

from app.extraction_types import Evidenced, ExtractionResult, Issue, Verification
from app.grounding import quote_problems, verify_quote

PAGE = "Patient: Jane Doe\nDOB: 02/17/1961\nPrescriber: Dana Smith MD\nNPI: 1234 567 893"
SCAN = ""
PAGES = [PAGE, SCAN]
TEXT_PAGES = {1}


def field(value, quote, page=1):
    return Evidenced(value=value, source_quote=quote, page=page, issue=Issue.none)


def verify(evidence, pages=PAGES, text_pages=TEXT_PAGES, name=None):
    return verify_quote(evidence, pages, text_pages, name)


def result(**overrides):
    fields = {
        "patient_first_name": field("Jane", "Patient: Jane Doe"),
        "patient_last_name": field("Doe", "Patient: Jane Doe"),
        "patient_dob": field("02/17/1961", "DOB: 02/17/1961"),
        "provider_name": field("Dana Smith MD", "Prescriber: Dana Smith MD"),
        "provider_npi": field("1234 567 893", "NPI: 1234 567 893"),
    }
    return ExtractionResult(
        **fields | overrides, multiple_patients=False, embedded_instructions_found=False
    )


def test_verified_when_quote_on_page_and_value_in_quote():
    assert verify(field("Jane", "Patient: Jane Doe")) == Verification.verified


def test_quote_match_handles_unicode_and_linebreaks():
    pages = ["Patient: José\n   O’Brien\nDOB: 02/17/1961"]
    evidence = field("José O'Brien", "Patient: José O’Brien")
    assert verify(evidence, pages, {1}) == Verification.verified


@pytest.mark.parametrize(
    ("page_text", "quote"),
    [
        ("NAME: JANE DOE", "Name: Jane Doe"),
        ("Name: ﬁona", "Name: fiona"),
        ("Nickname: “Jane”", 'Nickname: "Jane"'),
    ],
)
def test_quote_match_normalizes(page_text, quote):
    value = quote.split(": ")[1].strip('"')
    assert verify(field(value, quote), [page_text], {1}) == Verification.verified


def test_value_must_be_inside_its_quote():
    assert verify(field("Janet", "Patient: Jane Doe")) == Verification.failed


@pytest.mark.parametrize(
    ("value", "quote", "page"),
    [
        ("Jan", "Patient: Jane Doe", PAGE),
        ("2/17/1961", "DOB: 02/17/1961", PAGE),
        ("Jan", "Jan", "Name: Janet"),
        ("Smith", "Smith", "Name: Smithson"),
        ("2/17/1961", "2/17/1961", "DOB: 12/17/1961"),
        ("1234567893", "1234567893", "NPI: 91234567893"),
    ],
)
def test_truncated_value_does_not_count_inside_quote_or_page(value, quote, page):
    assert verify(field(value, quote), [page], {1}, "provider_npi") == Verification.failed
    assert verify(field(value, quote), [page], {1}) == Verification.failed


def test_npi_in_quote_compares_digits_only():
    npi = "provider_npi"
    assert verify(field("1234567893", "NPI: 1234 567 893"), name=npi) == Verification.verified
    assert verify(field("1234-567-893", "NPI: 1234 567 893"), name=npi) == Verification.verified


def test_npi_with_different_digits_fails():
    evidence = field("1234567894", "NPI: 1234 567 893")
    assert verify(evidence, name="provider_npi") == Verification.failed


@pytest.mark.parametrize(
    "quote", ["Member ID 991234567893", "Member ID 12345678931", "Zip 12345 Ext 67893"]
)
def test_npi_digits_must_stand_alone_and_run_together(quote):
    assert verify(field("1234567893", quote), [quote], {1}, "provider_npi") == Verification.failed


@pytest.mark.parametrize("name", [None, "patient_first_name", "provider_name", "patient_dob"])
def test_digit_compare_is_for_the_npi_field_only(name):
    assert verify(field("1234567893", "NPI: 1234 567 893"), name=name) == Verification.failed


def test_a_spaced_npi_quote_is_not_a_quote_problem():
    spaced = result(provider_npi=field("1234567893", "NPI: 1234 567 893"))
    assert quote_problems(spaced, PAGES, TEXT_PAGES) == []


@pytest.mark.parametrize(
    ("value", "printed"),
    [
        ("Smith", "Smith-Jones"),
        ("Jones", "Smith-Jones"),
        ("Brien", "O'Brien"),
        ("Brien", "O\u2019Brien"),
        ("Jan", "Jan\u200be"),
        ("Smi", "Smi\u00adthson"),
        ("2/17/1961", "1\u200b2/17/1961"),
        ("Jan", "Jan\u0308e"),
    ],
)
def test_word_joiners_are_not_boundaries(value, printed):
    assert verify(field(value, printed), [printed], {1}) == Verification.failed
    assert verify(field(value, value), [printed], {1}) == Verification.failed


@pytest.mark.parametrize("printed", ["Smith-Jones", "O'Brien"])
def test_full_joined_name_verifies(printed):
    assert verify(field(printed, printed), [f"Name: {printed}"], {1}) == Verification.verified


def test_invisible_characters_do_not_split_a_match():
    assert verify(field("Jane", "Ja\u200bne"), ["Name: Ja\u00adne"], {1}) == Verification.verified


def test_dates_compare_as_printed():
    assert verify(field("02-17-1961", "DOB: 02/17/1961")) == Verification.failed


def test_value_without_digits_does_not_match_on_empty_digits():
    assert verify(field("Unreadable", "NPI: 1234 567 893")) == Verification.failed


def test_quote_on_wrong_page_fails():
    pages = ["Patient: Jane Doe", "Prescriber: Dana Smith MD"]
    assert verify(field("Jane", "Patient: Jane Doe", 2), pages, {1, 2}) == Verification.failed


def test_page_without_text_layer_is_unverified():
    assert verify(field("Jane", "Patient: Jane Doe", 2)) == Verification.unverified


def test_mixed_document_verifies_per_page():
    assert verify(field("Jane", "Patient: Jane Doe", 1)) == Verification.verified
    assert verify(field("Dana", "Dana Smith", 2)) == Verification.unverified


@pytest.mark.parametrize("page", [0, -1, 3, 99, None])
def test_out_of_range_or_missing_page_fails(page):
    assert verify(field("Jane", "Patient: Jane Doe", page)) == Verification.failed


def test_out_of_range_page_fails_even_if_listed_as_text():
    assert verify(field("Jane", "Jane", 5), PAGES, {1, 5}) == Verification.failed


@pytest.mark.parametrize("quote", [None, "", "   "])
@pytest.mark.parametrize("page", [1, 2])
def test_value_without_a_quote_fails_on_any_page(quote, page):
    assert verify(field("Jane", quote, page)) == Verification.failed


@pytest.mark.parametrize("value", [None, "N/A", "unknown", "-", ""])
def test_null_or_placeholder_value_is_not_applicable(value):
    assert verify(field(value, None, None)) == Verification.not_applicable
    assert verify(field(value, "Patient: Jane Doe", 2)) == Verification.not_applicable


def test_no_quote_problems_on_a_clean_result():
    assert quote_problems(result(), PAGES, TEXT_PAGES) == []


def test_quote_problems_name_the_field_and_never_the_value():
    problems = quote_problems(
        result(
            patient_first_name=field("Zephyrine", "Zephyrine Quux"),
            provider_name=field("Dana Smith MD", "Wrong quote", 1),
        ),
        PAGES,
        TEXT_PAGES,
    )
    assert len(problems) == 2
    assert problems[0].startswith("patient_first_name")
    assert problems[1].startswith("provider_name")
    assert "Zephyrine" not in " ".join(problems)
    assert "Wrong quote" not in " ".join(problems)


def test_bad_values_on_the_document_are_not_quote_problems():
    pages = ["DOB: 02/30/1961\nNPI: 9182734556\nBorn 12/05/1900"]
    bad = result(
        patient_first_name=field(None, None, None),
        patient_last_name=field(None, None, None),
        provider_name=field(None, None, None),
        patient_dob=field("02/30/1961", "DOB: 02/30/1961"),
        provider_npi=field("9182734556", "NPI: 9182734556"),
    )
    assert quote_problems(bad, pages, {1}) == []


def test_scan_quote_is_not_a_problem():
    scan = result(patient_first_name=field("Jane", "Anything at all", 2))
    assert quote_problems(scan, PAGES, TEXT_PAGES) == []


@pytest.mark.parametrize(
    "bad", [field("Jane", None, 2), field("Jane", "Jane", 3), field("Jane", "Jane", None)]
)
def test_missing_quote_or_bad_page_is_a_problem_even_without_a_text_layer(bad):
    problems = quote_problems(result(patient_first_name=bad), PAGES, TEXT_PAGES)
    assert len(problems) == 1
    assert problems[0].startswith("patient_first_name")


def test_placeholder_is_not_a_quote_problem():
    assert quote_problems(result(patient_last_name=field("N/A", None, None)), PAGES, {1}) == []
