"""Upload the messy faxes to a running service and score the Orders against expected.json.

Run from backend/: uv run python -m scripts.eval_messy --base-url URL --dir PATH

SIGNUP_CODE comes from the environment. Output names fields, statuses and flag codes only, never a
patient value, password, token or the code, so it is safe to paste. Each file costs one upload of
the 20 a user gets per day, and a retry costs another if the first attempt stored the document.
"""

import argparse
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx

from app.extraction_types import FIELD_NAMES
from app.validators import has_two_digit_year, parse_dob
from scripts.make_messy_faxes import ExpectedFile

UPLOAD_TIMEOUT_SECONDS = 200
TITLES = {"dr", "md", "do", "np", "pa", "pa-c", "rn", "dpm", "fnp", "aprn", "phd", "mbbs"}


class QuotaReached(Exception):
    pass


@dataclass
class Comparison:
    fields: dict[str, bool]
    status: str
    codes: list[str]
    flags_ok: bool
    status_ok: bool
    missing_flags: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(self.fields.values()) and self.flags_ok and self.status_ok


def _name_words(value: str) -> set[str]:
    """Order, case, titles and initials are formatting. `Marwick, E` still isn't `Elena Marwick`."""
    words = re.findall(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", value.lower().replace(".", ""))
    return {word for word in words if word not in TITLES and len(word) > 1}


def _same_dob(expected: str, value: str, printed: str | None) -> bool:
    if has_two_digit_year(expected):
        # The century is unknowable, so only the digits as printed count as correct.
        return (printed or "").strip() == expected
    today = date.today()
    return parse_dob(value, today) == parse_dob(expected, today)


def _same(name: str, expected: str, value: str, printed: str | None) -> bool:
    if name == "patient_dob":
        return _same_dob(expected, value, printed)
    if name == "provider_npi":
        return re.sub(r"\D", "", value) == re.sub(r"\D", "", expected)
    return _name_words(value) == _name_words(expected)


def _field_passes(name: str, entry: ExpectedFile, order: dict, printed: str | None) -> bool:
    expected = getattr(entry, name)
    value = order.get(name)
    if value is None:
        return expected is None
    allowed = [*([expected] if expected else []), *entry.alternatives.get(name, [])]
    return any(_same(name, option, str(value), printed) for option in allowed)


def compare(entry: ExpectedFile, order: dict) -> Comparison:
    printed = {item["field"]: item.get("extracted_value") for item in order.get("evidence", [])}
    fields = {name: _field_passes(name, entry, order, printed.get(name)) for name in FIELD_NAMES}
    codes = sorted({flag["code"] for flag in order.get("flags", [])})
    missing = sorted({str(code.value) for code in entry.required_flags} - set(codes))
    status = order.get("status", "unknown")
    return Comparison(
        fields, status, codes, not missing, status not in entry.forbidden_status, missing
    )


def _register(client: httpx.Client, signup_code: str) -> dict[str, str]:
    """A throwaway user, so the run starts with a full quota and leaves nothing to log in to."""
    email = f"messy-eval-{secrets.token_hex(8)}@example.com"
    password = secrets.token_urlsafe(24)
    response = client.post(
        "/auth/register",
        json={"email": email, "password": password, "signup_code": signup_code},
    )
    if response.status_code != 201:
        sys.exit(f"Register failed: HTTP {response.status_code}")
    response = client.post("/auth/token", data={"username": email, "password": password})
    if response.status_code != 200:
        sys.exit(f"Login failed: HTTP {response.status_code}")
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _upload(client: httpx.Client, path: Path, headers: dict[str, str]) -> httpx.Response:
    files = {"file": (path.name, path.read_bytes(), "application/pdf")}
    response = client.post(
        "/v1/orders/upload", files=files, headers=headers, timeout=UPLOAD_TIMEOUT_SECONDS
    )
    if response.status_code == 503:
        response = client.post(
            "/v1/orders/upload", files=files, headers=headers, timeout=UPLOAD_TIMEOUT_SECONDS
        )
    return response


def _report(entry: ExpectedFile, result: Comparison) -> None:
    verdict = "PASS" if result.passed else "FAIL"
    print(f"{entry.file}: {verdict} status={result.status} flags={','.join(result.codes)}")
    print("  " + " ".join(f"{n}={'pass' if ok else 'FAIL'}" for n, ok in result.fields.items()))
    if result.missing_flags:
        print(f"  missing_flags={','.join(result.missing_flags)}")
    if not result.status_ok:
        print("  forbidden_status")


def run(client: httpx.Client, directory: Path, signup_code: str) -> int:
    """Score every file, one at a time. Returns the process exit code."""
    raw = json.loads((directory / "expected.json").read_text(encoding="utf-8"))
    entries = [ExpectedFile.model_validate(item) for item in raw]
    headers = _register(client, signup_code)
    files_passed = fields_passed = 0
    for entry in entries:
        try:
            response = _upload(client, directory / entry.file, headers)
        except httpx.HTTPError as error:
            print(f"{entry.file}: FAIL {type(error).__name__}")  # its text can hold the URL only
            continue
        if response.status_code == 429:
            print(f"{entry.file}: stopped, daily quota reached (HTTP 429)")
            break
        if response.status_code != 201:
            print(f"{entry.file}: FAIL HTTP {response.status_code}")
            continue
        result = compare(entry, response.json())
        _report(entry, result)
        files_passed += result.passed
        fields_passed += sum(result.fields.values())
    total_fields = len(entries) * len(FIELD_NAMES)
    print(f"TOTAL: {files_passed}/{len(entries)} files, {fields_passed}/{total_fields} fields")
    return 0 if files_passed == len(entries) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--dir", type=Path, required=True)
    args = parser.parse_args(argv)
    signup_code = os.environ.get("SIGNUP_CODE")
    if not signup_code:
        sys.exit("SIGNUP_CODE is not set.")
    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        return run(client, args.dir, signup_code)


if __name__ == "__main__":
    sys.exit(main())
