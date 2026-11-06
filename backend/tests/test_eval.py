import inspect
import os

import httpx
import pytest

from app.config import settings
from scripts.eval import FIXTURES, SAMPLE_EXPECTED, EvalError, main, run, score
from tests import pdf_fixtures as fx
from tests.llm_stub import clean_result, evidenced, reply

# conftest sets this once signup is gated. Until then the route ignores it.
SIGNUP_CODE = os.environ.get("SIGNUP_CODE", "test-signup-code")
PATIENT_VALUES = [fx.FIRST_NAME, fx.LAST_NAME, fx.DOB, "1962-03-14", fx.PROVIDER, fx.NPI]


def order(**overrides) -> dict:
    """An upload response, as the API sends it."""
    row = {"field": "patient_first_name", "source_quote": "quote", "verification": "verified"}
    return {
        "http_status": 201,
        "status": "approved",
        "patient_first_name": fx.FIRST_NAME,
        "provider_name": "Arjun Raj D.P.M",
        "evidence": [row],
        "flags": [],
        **overrides,
    }


def replies(**swaps) -> list:
    """One reply per fixture that reaches the model, in FIXTURES order."""
    results = {
        "clean_text": clean_result(),
        "bad_npi": clean_result(
            provider_npi=evidenced(fx.BAD_NPI, f"Prescriber NPI: {fx.BAD_NPI}")
        ),
        "two_providers": clean_result(),
        "missing_field": clean_result(patient_dob=evidenced(None, None, None)),
        "injected": clean_result(embedded_instructions_found=True),
        "scan": clean_result(),
        "blank_page": clean_result(),
        **swaps,
    }
    return [reply(results[name]) for name in FIXTURES if name in results]


def test_score_matches_and_reports_field_names_only():
    expected = {"status": "approved", "patient_first_name": fx.FIRST_NAME}
    assert score(expected, order()) == []

    wrong = order(status="needs_review", patient_first_name="Somebody Else")
    mismatches = score(expected, wrong)

    assert mismatches == ["status", "patient_first_name"]
    assert "Somebody Else" not in str(mismatches)
    assert fx.FIRST_NAME not in str(mismatches)


def test_score_needs_each_listed_flag_and_allows_others():
    actual = order(flags=[{"code": "invalid_npi"}, {"code": "unverified_quote"}])
    assert score({"flags": ["invalid_npi"]}, actual) == []
    assert score({"flags": ["invalid_npi", "implausible_dob"]}, actual) == ["flags"]


def test_score_checks_every_quote_but_skips_absent_values():
    rows = [
        {"verification": "unverified"},
        {"verification": "not_applicable"},
    ]
    assert score({"verification": "unverified"}, order(evidence=rows)) == []

    rows.append({"verification": "verified"})
    assert score({"verification": "unverified"}, order(evidence=rows)) == ["verification"]


def test_score_does_not_pass_verification_with_no_evidence():
    # A failed extraction saves no evidence, and "every quote" must not hold on an empty list.
    assert score({"verification": "unverified"}, order(evidence=[])) == ["verification"]


def test_score_contains_reports_the_field_it_read():
    assert score({"provider_name_contains": "Raj"}, order()) == []
    assert score({"provider_name_contains": "Raj"}, order(provider_name=None)) == ["provider_name"]


def test_fixtures_fit_within_the_daily_quota():
    stored = [name for name, (_, expected) in FIXTURES.items() if expected["http_status"] == 201]
    assert len(stored) + 1 <= settings.quota_user_daily  # the 1 is --pdf


def test_every_generated_fixture_is_evaluated():
    used = {build.__name__ for build, _ in FIXTURES.values()}
    used |= {"many_pages_pdf"}  # takes a page count, so FIXTURES holds a wrapper
    builders = {
        name
        for name, value in vars(fx).items()
        if inspect.isfunction(value) and name.endswith("_pdf") and name != "text_pdf"
    }
    assert builders <= used


def test_run_passes_on_the_real_routes_and_prints_no_values(client, stub_llm, capsys):
    stub_llm(*replies())

    assert run(client, SIGNUP_CODE, None) is True

    out = capsys.readouterr().out
    assert len(out.splitlines()) == len(FIXTURES) + 1  # one per fixture, then the total
    assert "scan PASS" in out
    assert "patient_first_name=unverified" in out
    assert "invalid_npi" in out
    for secret in [*PATIENT_VALUES, fx.BAD_NPI, SIGNUP_CODE, "Bearer", "@example.com"]:
        assert secret not in out


def test_run_fails_and_names_the_field_but_not_the_value(client, stub_llm, capsys):
    wrong = clean_result(provider_name=evidenced(fx.REFERRING, f"Referred by {fx.REFERRING}"))
    stub_llm(*replies(scan=wrong))

    assert run(client, SIGNUP_CODE, None) is False

    out = capsys.readouterr().out
    assert "scan FAIL" in out
    assert "failed=provider_name" in out
    assert fx.REFERRING not in out


def test_run_adds_the_sample_with_its_own_checks(client, stub_llm, capsys):
    sample = clean_result(
        patient_dob=evidenced("12/05/1900", "DOB 12/05/1900"),
        provider_name=evidenced("Arjun Raj D.P.M", "Prescriber: Arjun Raj D.P.M"),
        provider_npi=evidenced("9182734556", "NPI 9182734556"),
    )
    stub_llm(*replies(), reply(sample))

    assert run(client, SIGNUP_CODE, fx.scan_pdf()) is True

    out = capsys.readouterr().out
    assert "sample PASS" in out
    assert "implausible_dob" in out
    assert "Raj" not in out
    assert "9182734556" not in out


def test_sample_checks_refuse_a_failed_extraction():
    failed = order(status="needs_review", provider_name=None, evidence=[], flags=[])
    failed["flags"] = [{"code": "extraction_failed"}]
    assert set(score(SAMPLE_EXPECTED, failed)) == {"flags", "verification", "provider_name"}


def test_a_refused_signup_stops_the_run_without_echoing_the_code():
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "Signup is not available."})

    transport = httpx.MockTransport(refuse)
    with httpx.Client(transport=transport, base_url="http://eval.test") as refused:
        with pytest.raises(EvalError) as stopped:
            run(refused, SIGNUP_CODE, None)

    assert "403" in str(stopped.value)
    assert SIGNUP_CODE not in str(stopped.value)


def test_a_network_failure_on_one_fixture_is_a_failure_not_a_crash(capsys):
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/orders/upload":
            raise httpx.ReadTimeout("timed out", request=request)
        if request.url.path == "/auth/token":
            return httpx.Response(200, json={"access_token": "a-token", "token_type": "bearer"})
        return httpx.Response(201, json={"id": "1", "email": "x"})

    transport = httpx.MockTransport(respond)
    with httpx.Client(transport=transport, base_url="http://eval.test") as timing_out:
        assert run(timing_out, SIGNUP_CODE, None) is False

    out = capsys.readouterr().out
    assert out.count("FAIL") == len(FIXTURES)
    assert "error=ReadTimeout" in out
    assert "a-token" not in out


def test_cli_needs_the_signup_code(monkeypatch, capsys):
    monkeypatch.delenv("SIGNUP_CODE", raising=False)

    assert main(["--base-url", "http://eval.test"]) != 0
    assert "SIGNUP_CODE" in capsys.readouterr().err


def test_cli_refuses_an_unreadable_pdf_before_spending_anything(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SIGNUP_CODE", SIGNUP_CODE)
    missing = tmp_path / "Wren Okonkwo-Bell order.pdf"

    assert main(["--base-url", "http://eval.test", "--pdf", str(missing)]) != 0

    captured = capsys.readouterr()
    assert "--pdf" in captured.err
    assert fx.FIRST_NAME not in captured.err + captured.out  # a path can hold a patient's name
