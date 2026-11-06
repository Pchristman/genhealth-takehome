"""Run the generated PDFs, and optionally GenHealth's sample, through a running service.

From backend/, with SIGNUP_CODE in the environment:
    uv run python -m scripts.eval --base-url URL [--pdf PATH]

Registers a new throwaway user each run, so the per-user quota starts empty. Each stored fixture
spends one model call and one of the service's daily uploads. The refused files spend neither.

Output is field names, pass or fail, status, flag codes and verification values. Patient values,
credentials and response bodies are never printed, and what a server sends back is only printed
if it is one of the known words below.
"""

import argparse
import os
import secrets
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx

from app.extraction_types import FIELD_NAMES, FlagCode, Verification
from app.models import OrderStatus
from tests import pdf_fixtures as fx

# The server's model deadline is 240 s, and a client that gives up first loses a saved result.
TIMEOUT = httpx.Timeout(300, connect=30)
PAGE_LIMIT = 100  # settings.upload_max_pages, not imported: reading settings needs a JWT secret


class EvalError(Exception):
    """Stops the run. The message is fixed text and a status code, never a response body."""


def _over_page_limit_pdf() -> bytes:
    return fx.many_pages_pdf(PAGE_LIMIT + 1)


_CLEAN = {
    "http_status": 201,
    "status": "approved",
    "verification": "verified",
    "patient_first_name": fx.FIRST_NAME,
    "patient_last_name": fx.LAST_NAME,
    "patient_dob": datetime.strptime(fx.DOB, "%m/%d/%Y").date().isoformat(),
    "provider_name": fx.PROVIDER,
    "provider_npi": fx.NPI,
}
_NEEDS_REVIEW = {**_CLEAN, "status": "needs_review"}

FIXTURES: dict[str, tuple[Callable[[], bytes], dict]] = {
    "clean_text": (fx.clean_text_pdf, _CLEAN),
    "bad_npi": (
        fx.bad_npi_pdf,
        {**_NEEDS_REVIEW, "provider_npi": fx.BAD_NPI, "flags": ["invalid_npi"]},
    ),
    "two_providers": (fx.two_providers_pdf, _CLEAN),
    "missing_field": (
        fx.missing_field_pdf,
        {**_NEEDS_REVIEW, "patient_dob": None, "flags": ["missing"]},
    ),
    "injected": (fx.injected_pdf, {**_NEEDS_REVIEW, "flags": ["embedded_instructions"]}),
    "scan": (
        fx.scan_pdf,
        {**_NEEDS_REVIEW, "verification": "unverified", "flags": ["unverified_quote"]},
    ),
    "searchable_scan": (
        fx.searchable_scan_pdf,
        {**_NEEDS_REVIEW, "verification": "unverified", "flags": ["unverified_quote"]},
    ),
    "blank_page": (fx.blank_page_pdf, _CLEAN),
    # Refused before the model is called, so these three use no quota.
    "encrypted": (fx.encrypted_pdf, {"http_status": 422}),
    "corrupt": (fx.corrupt_pdf, {"http_status": 422}),
    "over_page_limit": (_over_page_limit_pdf, {"http_status": 413}),
}

SAMPLE_EXPECTED = {
    "http_status": 201,
    "status": "needs_review",
    "flags": ["invalid_npi", "implausible_dob"],
    "verification": "unverified",
    "provider_name_contains": "Raj",
}


def _flag_codes(actual: dict) -> list[str]:
    return [flag.get("code") for flag in actual.get("flags") or []]


def _verification_holds(want: str, actual: dict) -> bool:
    """Every value's quote has this verification. No evidence at all is a failure, not a pass."""
    found = [row.get("verification") for row in actual.get("evidence") or []]
    quoted = [value for value in found if value != Verification.not_applicable]
    return bool(quoted) and all(value == want for value in quoted)


def score(expected: dict, actual: dict) -> list[str]:
    """The expected keys that `actual` fails, as field names. Values never appear in the result.

    Each key is compared for equality, apart from these:
      flags: every code listed is present; others may be too
      verification: every quote of a value has this verification
      provider_name_contains: reported as provider_name
    """
    mismatches = []
    for key, want in expected.items():
        if key == "flags":
            passed = set(want) <= set(_flag_codes(actual))
        elif key == "verification":
            passed = _verification_holds(want, actual)
        elif key == "provider_name_contains":
            key = "provider_name"
            passed = want.casefold() in (actual.get(key) or "").casefold()
        else:
            passed = actual.get(key) == want
        if not passed:
            mismatches.append(key)
    return mismatches


def _known(value: object, allowed: set[str]) -> str:
    if value is None:
        return "-"
    return value if value in allowed else "unknown"


def _line(name: str, actual: dict, mismatches: list[str], error: str | None) -> str:
    codes = sorted({_known(code, set(FlagCode)) for code in _flag_codes(actual)})
    rows = [
        f"{row.get('field')}={_known(row.get('verification'), set(Verification))}"
        for row in actual.get("evidence") or []
        if row.get("field") in FIELD_NAMES
    ]
    parts = [
        name,
        "FAIL" if mismatches else "PASS",
        f"http={actual.get('http_status', '-')}",  # set by _upload, so never from the body
        f"status={_known(actual.get('status'), set(OrderStatus))}",
        f"flags={','.join(codes) or '-'}",
        f"verification={','.join(rows) or '-'}",
        f"failed={','.join(mismatches) or '-'}",
    ]
    if error:
        parts.append(f"error={error}")
    return " ".join(parts)


def _token(client: httpx.Client, signup_code: str) -> str:
    email = f"eval-{uuid4().hex}@example.com"
    password = secrets.token_urlsafe(32)
    credentials = {"email": email, "password": password, "signup_code": signup_code}
    registered = client.post("/auth/register", json=credentials)
    if registered.status_code != 201:
        raise EvalError(f"register returned {registered.status_code}")
    login = client.post("/auth/token", data={"username": email, "password": password})
    if login.status_code != 200:
        raise EvalError(f"login returned {login.status_code}")
    return login.json()["access_token"]


def _upload(client: httpx.Client, token: str, data: bytes) -> dict:
    # A fixed filename: a real one can hold a patient's name, and the service ignores it anyway.
    response = client.post(
        "/v1/orders/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("order.pdf", data, "application/pdf")},
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    return {**(body if isinstance(body, dict) else {}), "http_status": response.status_code}


def run(client: httpx.Client, signup_code: str, sample: bytes | None) -> bool:
    """Print one line per file. True when every file met its expected results."""
    token = _token(client, signup_code)

    cases = [(name, build, expected) for name, (build, expected) in FIXTURES.items()]
    if sample is not None:
        cases.append(("sample", lambda: sample, SAMPLE_EXPECTED))

    failures = 0
    for name, build, expected in cases:
        error = None
        try:
            actual = _upload(client, token, build())
        except httpx.HTTPError as failure:  # the class name only: a message can carry a URL
            actual, error = {}, type(failure).__name__
        mismatches = score(expected, actual)
        failures += bool(mismatches)
        print(_line(name, actual, mismatches, error))

    print(f"total={len(cases)} passed={len(cases) - failures} failed={failures}")
    return failures == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a running service against known PDFs.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--pdf", type=Path, help="GenHealth's sample, checked by its own rules")
    args = parser.parse_args(argv)

    signup_code = os.environ.get("SIGNUP_CODE")
    if not signup_code:
        print("SIGNUP_CODE is not set.", file=sys.stderr)
        return 1

    sample = None
    if args.pdf is not None:
        try:
            sample = args.pdf.read_bytes()
        except OSError:
            print("--pdf could not be read.", file=sys.stderr)  # the path can hold a patient's name
            return 1

    with httpx.Client(base_url=args.base_url, timeout=TIMEOUT) as client:
        try:
            return 0 if run(client, signup_code, sample) else 1
        except EvalError as stopped:
            print(f"Stopped: {stopped}", file=sys.stderr)
        except httpx.HTTPError as failure:
            print(f"Stopped: {type(failure).__name__}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
