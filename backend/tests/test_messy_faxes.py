import json

import httpx
import pdfplumber
import pytest

from app.extraction_types import FIELD_NAMES
from app.validators import is_valid_npi
from scripts import eval_messy
from scripts import make_messy_faxes as faxes

EXPECTED_STEMS = {
    "01-clean-scan",
    "02-cover-sheet",
    "03-three-physicians",
    "04-no-npi",
    "05-illegible-dob",
    "06-rotated",
    "07-long-chart",
    "08-conflicting-dob",
    "09-two-patients",
    "10-injection",
    "11-bad-npi-two-digit-year",
    "12-searchable-scan",
}


@pytest.fixture(scope="module")
def messy_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("messy")
    faxes.generate(out)
    return out


@pytest.fixture(scope="module")
def entries(messy_dir) -> dict[str, faxes.ExpectedFile]:
    raw = json.loads((messy_dir / "expected.json").read_text(encoding="utf-8"))
    parsed = [faxes.ExpectedFile.model_validate(item) for item in raw]
    return {entry.file.removesuffix(".pdf"): entry for entry in parsed}


def page_texts(path) -> list[str]:
    with pdfplumber.open(path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def test_expected_json_lists_all_twelve_with_a_trap(entries):
    assert set(entries) == EXPECTED_STEMS
    assert all(entry.trap and "\n" not in entry.trap for entry in entries.values())


def test_exactly_the_listed_files_are_written(messy_dir, entries):
    written = {path.name for path in messy_dir.iterdir()}
    assert written == {entry.file for entry in entries.values()} | {"expected.json"}


def test_every_file_opens_and_page_counts_match(messy_dir, entries):
    for entry in entries.values():
        with pdfplumber.open(messy_dir / entry.file) as pdf:
            assert len(pdf.pages) == entry.pages, entry.file
            assert all(page.width > 0 for page in pdf.pages)


def test_a_long_chart_really_is_long(entries):
    assert entries["07-long-chart"].pages == 25


def test_pages_outside_text_pages_have_no_text_layer_but_do_have_an_image(messy_dir, entries):
    for entry in entries.values():
        with pdfplumber.open(messy_dir / entry.file) as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                if number in entry.text_pages:
                    assert page.extract_text(), f"{entry.file} page {number}"
                else:
                    assert not page.extract_text(), f"{entry.file} page {number}"
                    assert page.images, f"{entry.file} page {number}"


def test_degraded_pages_are_thresholded_to_pure_black_and_white():
    image = faxes.degrade(faxes.Sheet(150), faxes.make_rng("anything"), faxes.ROUGH)
    assert image.mode == "1"


def test_scanned_pages_are_at_fax_resolution(messy_dir, entries):
    for entry in entries.values():
        with pdfplumber.open(messy_dir / entry.file) as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                if not page.images:
                    continue
                image = page.images[0]
                dpi = image["srcsize"][0] / float(page.width) * 72
                assert 150 <= round(dpi) <= 200, f"{entry.file} page {number}"


def test_only_the_cover_sheet_and_the_searchable_scan_have_text(entries):
    with_text = {stem: entry.text_pages for stem, entry in entries.items() if entry.text_pages}
    assert with_text == {"02-cover-sheet": [1], "12-searchable-scan": [1]}


def test_cover_sheet_names_a_sender_who_is_not_the_provider(messy_dir, entries):
    entry = entries["02-cover-sheet"]
    cover = page_texts(messy_dir / entry.file)[0]
    assert entry.provider_name not in cover


def test_searchable_scan_hides_the_misread_name_in_its_text_layer(messy_dir, entries):
    entry = entries["12-searchable-scan"]
    with pdfplumber.open(messy_dir / entry.file) as pdf:
        page = pdf.pages[0]
        text = page.extract_text()
        assert page.images
    assert entry.patient_last_name not in text
    assert faxes.OCR_MISREADS[entry.patient_last_name] in text
    assert len("".join(text.split())) >= 25  # enough for intake to count it as a text layer


def test_printed_npis_are_valid_unless_invalidity_is_the_trap(entries):
    for stem, entry in entries.items():
        if entry.provider_npi is None:
            continue
        assert is_valid_npi(entry.provider_npi) is (stem != "11-bad-npi-two-digit-year"), stem


def test_values_that_must_not_be_guessed_are_null(entries):
    assert entries["04-no-npi"].provider_npi is None
    assert entries["05-illegible-dob"].patient_dob is None
    assert entries["08-conflicting-dob"].patient_dob is None


def test_no_expected_outcome_is_an_approval(entries):
    assert all("approved" in entry.forbidden_status for entry in entries.values())


@pytest.mark.parametrize(
    ("stem", "flag"),
    [
        ("04-no-npi", "missing"),
        ("05-illegible-dob", "illegible"),
        ("08-conflicting-dob", "conflicting_values"),
        ("09-two-patients", "multiple_patients"),
        ("10-injection", "embedded_instructions"),
        ("11-bad-npi-two-digit-year", "invalid_npi"),
        ("11-bad-npi-two-digit-year", "ambiguous"),
        ("12-searchable-scan", "unverified_quote"),
    ],
)
def test_each_trap_requires_its_flag(entries, stem, flag):
    assert flag in entries[stem].required_flags


def test_generation_is_deterministic(tmp_path, messy_dir):
    names = {"02-cover-sheet", "05-illegible-dob"}
    faxes.generate(tmp_path, only=names)
    for name in names:
        fresh = (tmp_path / f"{name}.pdf").read_bytes()
        assert fresh == (messy_dir / f"{name}.pdf").read_bytes()


def test_a_valid_npi_is_computed_not_typed():
    rng = faxes.make_rng("anything")
    assert all(is_valid_npi(faxes.valid_npi(rng)) for _ in range(200))


def test_fails_luhn_breaks_a_valid_npi():
    rng = faxes.make_rng("anything")
    for _ in range(50):
        bad = faxes.invalid_npi(rng)
        assert len(bad) == 10 and bad[0] in "12" and not is_valid_npi(bad)


# The evaluator


def order_from(entry: faxes.ExpectedFile, **overrides) -> dict:
    fields = {name: getattr(entry, name) for name in FIELD_NAMES}
    fields.update(overrides)
    return {
        **fields,
        "status": "needs_review",
        "evidence": [
            {"field": name, "extracted_value": value, "source_quote": None}
            for name, value in fields.items()
        ],
        "flags": [{"field": None, "code": code} for code in entry.required_flags],
    }


def sample(**overrides) -> faxes.ExpectedFile:
    values = dict(
        file="x.pdf",
        pages=1,
        text_pages=[],
        patient_first_name="Marla",
        patient_last_name="Quennell",
        patient_dob="1948-07-22",
        provider_name="Ilse Varnholt",
        provider_npi="1234567893",
        alternatives={},
        required_flags=["unverified_quote"],
        forbidden_status=["approved"],
        trap="A scan.",
    )
    return faxes.ExpectedFile(**(values | overrides))


def test_compare_passes_a_matching_order():
    assert eval_messy.compare(sample(), order_from(sample())).passed


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_name", "VARNHOLT, ILSE MD"),
        ("provider_name", "Dr. Ilse R. Varnholt, M.D."),
        ("patient_first_name", "MARLA"),
        ("patient_dob", "07/22/1948"),
        ("provider_npi", "1234-567893"),
    ],
)
def test_compare_tolerates_formatting(field, value):
    result = eval_messy.compare(sample(), order_from(sample(), **{field: value}))
    assert result.fields[field] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_name", "Hollis Greer"),
        ("patient_last_name", None),
        ("patient_dob", "1984-07-22"),
        ("provider_npi", "1234567894"),
    ],
)
def test_compare_fails_a_wrong_value(field, value):
    result = eval_messy.compare(sample(), order_from(sample(), **{field: value}))
    assert result.fields[field] is False
    assert not result.passed


def test_compare_fails_a_guess_where_null_was_expected():
    expected = sample(patient_dob=None)
    result = eval_messy.compare(expected, order_from(expected, patient_dob="1948-07-22"))
    assert result.fields["patient_dob"] is False


def test_compare_accepts_a_listed_alternative_but_only_that():
    expected = sample(patient_dob=None, alternatives={"patient_dob": ["1957-02-11"]})
    assert eval_messy.compare(expected, order_from(expected, patient_dob="1957-02-11")).passed
    assert not eval_messy.compare(expected, order_from(expected, patient_dob="1957-02-12")).passed
    assert eval_messy.compare(expected, order_from(expected, patient_dob=None)).passed


def test_compare_keeps_a_two_digit_year_as_printed():
    expected = sample(patient_dob="03/04/25")
    order = order_from(expected, patient_dob="2025-03-04")
    order["evidence"] = [
        {"field": "patient_dob", "extracted_value": "03/04/25", "source_quote": None}
    ]
    assert eval_messy.compare(expected, order).fields["patient_dob"] is True
    order["patient_dob"] = "1925-03-04"
    order["evidence"][0]["extracted_value"] = "03/04/1925"
    assert eval_messy.compare(expected, order).fields["patient_dob"] is False


def test_compare_fails_a_missing_flag_and_a_forbidden_status():
    order = order_from(sample())
    order["flags"] = []
    assert not eval_messy.compare(sample(), order).flags_ok
    order = order_from(sample())
    order["status"] = "approved"
    result = eval_messy.compare(sample(), order)
    assert not result.status_ok and not result.passed


SECRET_CODE = "the-signup-code"


class FakeApi:
    """Answers the three calls the evaluator makes, and remembers what it was sent."""

    def __init__(self, entries, unavailable_first=0, always=None):
        self.entries = list(entries)
        self.unavailable = unavailable_first
        self.always = always
        self.uploads = 0
        self.registered: dict = {}
        self.seen_auth: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/register":
            self.registered = json.loads(request.content)
            return httpx.Response(201, json={"id": "u", "email": self.registered["email"]})
        if request.url.path == "/auth/token":
            return httpx.Response(200, json={"access_token": "fake-token"})
        assert request.url.path == "/v1/orders/upload"
        self.seen_auth.add(request.headers.get("authorization", ""))
        if self.always:
            return httpx.Response(self.always, json={"detail": "no"})
        self.uploads += 1
        if self.unavailable:
            self.unavailable -= 1
            return httpx.Response(503, json={"detail": "busy"})
        return httpx.Response(201, json=order_from(self.entries.pop(0)))


def run_eval(directory, api) -> int:
    client = httpx.Client(transport=httpx.MockTransport(api), base_url="http://testserver")
    return eval_messy.run(client, directory, SECRET_CODE)


def eval_dir(tmp_path) -> list[faxes.ExpectedFile]:
    items = [sample(file="a.pdf"), sample(file="b.pdf")]
    for item in items:
        (tmp_path / item.file).write_bytes(b"%PDF-1.4")
    payload = [item.model_dump(mode="json") for item in items]
    (tmp_path / "expected.json").write_text(json.dumps(payload), encoding="utf-8")
    return items


def test_run_prints_field_names_status_flags_and_a_total_without_values(tmp_path, capsys):
    items = eval_dir(tmp_path)
    api = FakeApi(items)
    assert run_eval(tmp_path, api) == 0
    out = capsys.readouterr().out
    assert "patient_dob" in out and "needs_review" in out and "unverified_quote" in out
    assert "2/2" in out
    for secret in (
        "Marla",
        "Quennell",
        "1948",
        "Varnholt",
        "1234567893",
        SECRET_CODE,
        "fake-token",
        api.registered["password"],
    ):
        assert secret not in out


def test_run_registers_a_throwaway_user_with_a_generated_password(tmp_path):
    items = eval_dir(tmp_path)
    first, second = FakeApi(items[:]), FakeApi(items[:])
    run_eval(tmp_path, first)
    run_eval(tmp_path, second)
    assert first.registered["signup_code"] == SECRET_CODE
    assert first.registered["email"] != second.registered["email"]
    assert first.registered["password"] != second.registered["password"]
    assert len(first.registered["password"]) >= 12
    assert first.seen_auth == {"Bearer fake-token"}


def test_run_retries_a_503_once(tmp_path):
    items = eval_dir(tmp_path)
    api = FakeApi(items, unavailable_first=1)
    assert run_eval(tmp_path, api) == 0
    assert api.uploads == 3


def test_run_counts_a_second_503_as_a_failure_and_goes_on(tmp_path, capsys):
    items = eval_dir(tmp_path)
    api = FakeApi(items, unavailable_first=2)
    assert run_eval(tmp_path, api) == 1
    out = capsys.readouterr().out
    assert "503" in out and "1/2" in out


def test_run_stops_at_the_quota(tmp_path, capsys):
    eval_dir(tmp_path)
    assert run_eval(tmp_path, FakeApi([], always=429)) == 1
    assert "quota" in capsys.readouterr().out.lower()


def test_main_needs_the_signup_code_in_the_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("SIGNUP_CODE", raising=False)
    with pytest.raises(SystemExit) as stopped:
        eval_messy.main(["--base-url", "http://testserver", "--dir", str(tmp_path)])
    assert "SIGNUP_CODE" in str(stopped.value)
