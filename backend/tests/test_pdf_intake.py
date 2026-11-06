import json
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

import pdfplumber
import pytest
from reportlab.lib.pdfencrypt import StandardEncryption
from sqlmodel import select

from app import pdf_worker
from app.config import settings
from app.models import Document
from app.pdf_intake import UNREADABLE, ParsedPdf, PdfRejected, read_pdf
from tests import pdf_fixtures as fx


def upload(client, headers, data: bytes):
    return client.post(
        "/v1/orders/upload",
        headers=headers,
        files={"file": ("order.pdf", data, "application/pdf")},
    )


def assert_rejected(response, status_code: int, stub, db) -> None:
    assert response.status_code == status_code
    assert stub.calls == []
    assert db.exec(select(Document)).all() == []


def test_rejects_non_pdf_bytes_415(client, auth_headers, stub_llm, db):
    stub = stub_llm()
    response = upload(client, auth_headers(), b"just a text file, renamed to end in .pdf")
    assert_rejected(response, 415, stub, db)


def test_rejects_encrypted_pdf_422(client, auth_headers, stub_llm, db):
    stub = stub_llm()
    assert_rejected(upload(client, auth_headers(), fx.encrypted_pdf()), 422, stub, db)


def test_rejects_pdf_encrypted_with_a_blank_password_422(client, auth_headers, stub_llm, db):
    stub = stub_llm()
    data = fx.text_pdf([["Enough characters to count as a text layer"]], StandardEncryption(""))
    assert_rejected(upload(client, auth_headers(), data), 422, stub, db)


def test_rejects_corrupt_pdf_422(client, auth_headers, stub_llm, db):
    stub = stub_llm()
    assert_rejected(upload(client, auth_headers(), fx.corrupt_pdf()), 422, stub, db)


def test_rejects_oversize_413_and_too_many_pages_413(
    client, auth_headers, stub_llm, db, monkeypatch
):
    stub = stub_llm()
    headers = auth_headers()

    data = fx.clean_text_pdf()
    monkeypatch.setattr(settings, "upload_max_bytes", len(data) - 1)
    assert_rejected(upload(client, headers, data), 413, stub, db)
    monkeypatch.undo()

    monkeypatch.setattr(settings, "upload_max_pages", 3)
    assert_rejected(upload(client, headers, fx.many_pages_pdf(4)), 413, stub, db)


def test_a_file_exactly_at_the_limits_is_accepted():
    data = fx.many_pages_pdf(2)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(settings, "upload_max_bytes", len(data))
        patch.setattr(settings, "upload_max_pages", 2)
        assert read_pdf(data).page_count == 2


def test_blank_page_is_not_a_failure():
    parsed = read_pdf(fx.blank_page_pdf())
    assert parsed.page_count == 2
    assert parsed.pages[1] == ""
    assert parsed.text_pages == {1}


def test_scan_has_no_text_pages():
    parsed = read_pdf(fx.scan_pdf())
    assert parsed == ParsedPdf(pages=["", ""], text_pages=set(), page_count=2)


def test_text_layer_threshold_is_25_non_space_characters_per_page():
    just_short = " ".join("x" * 24)  # spaces must not count towards the threshold
    just_enough = "x" * 25
    parsed = read_pdf(fx.text_pdf([[just_short], [just_enough]]))
    assert parsed.text_pages == {2}


def test_a_pdf_with_no_pages_is_refused_by_the_worker(monkeypatch):
    class Empty:
        pages: list = []
        doc = type("Doc", (), {"encryption": None})

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr("app.pdf_worker.pdfplumber.open", lambda *_a, **_k: Empty())
    with pytest.raises(ValueError):
        pdf_worker.parse(fx.clean_text_pdf(), 100)


def test_rejection_details_are_json_safe_static_text():
    with pytest.raises(PdfRejected) as caught:
        read_pdf(b"not a pdf at all")
    assert json.dumps({"detail": caught.value.detail})
    assert "not a pdf at all" not in caught.value.detail


TYPED_PAGE = ["Line of real typed text, long enough to count as a text layer"]


def test_page_under_a_full_image_has_no_text_layer():
    assert read_pdf(fx.searchable_scan_pdf()).text_pages == set()


def test_text_over_half_or_more_image_has_no_text_layer():
    at_half = fx.image_over_text_pdf(TYPED_PAGE, invisible=False, image_share=0.5)
    assert read_pdf(at_half).text_pages == set()


def test_text_under_half_image_keeps_its_text_layer():
    below_half = fx.image_over_text_pdf(TYPED_PAGE, invisible=False, image_share=0.49)
    assert read_pdf(below_half).text_pages == {1}


@pytest.mark.parametrize("degrees", [90, 180, 270])
def test_a_rotated_page_counts_as_no_text_layer(degrees):
    assert read_pdf(fx.rotated_pdf(degrees)).text_pages == set()


def test_a_zero_rotation_page_keeps_its_text_layer():
    assert read_pdf(fx.rotated_pdf(0)).text_pages == {1}


def test_one_bad_page_is_no_text_layer_and_the_rest_survive(monkeypatch):
    real = pdfplumber.page.Page.extract_text

    def fails_on_page_two(page, *args, **kwargs):
        if page.page_number == 2:
            raise ValueError(fx.LAST_NAME)
        return real(page, *args, **kwargs)

    monkeypatch.setattr(pdfplumber.page.Page, "extract_text", fails_on_page_two)
    pages = pdf_worker.parse(fx.many_pages_pdf(3), 100)["pages"]

    assert pages[0] and pages[2]
    assert pages[1] == ""


def test_leading_bytes_before_the_header_are_accepted():
    parsed = read_pdf(b"\r\n\x00junk from a fax gateway\n" + fx.clean_text_pdf())
    assert parsed.page_count == 1
    assert parsed.text_pages == {1}


def test_a_header_at_the_end_of_the_first_kilobyte_is_found():
    assert read_pdf(b"\n" * 1019 + fx.clean_text_pdf()).page_count == 1


def test_a_header_past_the_first_kilobyte_is_a_415():
    with pytest.raises(PdfRejected) as caught:
        read_pdf(b"\n" * 1020 + fx.clean_text_pdf())
    assert caught.value.status_code == 415


def test_flate_bomb_is_a_422_within_the_timeout_and_the_parent_stays_small(monkeypatch):
    bomb = fx.flate_bomb_pdf()
    assert len(bomb) < 1_000_000
    monkeypatch.setattr(settings, "pdf_parse_timeout_seconds", 2)

    tracemalloc.start()
    started = time.monotonic()
    with pytest.raises(PdfRejected) as caught:
        read_pdf(bomb)
    elapsed = time.monotonic() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert caught.value.status_code == 422
    assert caught.value.detail == UNREADABLE
    assert elapsed < 15
    assert peak < 20_000_000


def fake_worker(monkeypatch, *, stdout=b"", stderr=b"", returncode=0, raises=None) -> list[dict]:
    calls: list[dict] = []

    def run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    monkeypatch.setattr("app.pdf_intake.subprocess.run", run)
    return calls


@pytest.mark.parametrize(
    "outcome",
    [
        {"returncode": 1, "stdout": b'{"pages": ["looks fine"]}'},
        {"returncode": -9},
        {"stdout": b"not json"},
        {"stdout": b"[1, 2]"},
        {"stdout": b'{"pages": []}'},
        {"stdout": b'{"pages": [1]}'},
        {"stdout": b"{}"},
        {"raises": subprocess.TimeoutExpired("worker", 30, output=b"x", stderr=b"y")},
        {"raises": OSError("could not start")},
    ],
)
def test_a_failed_worker_is_a_fixed_422(monkeypatch, outcome):
    stderr = f"Traceback: {fx.LAST_NAME}".encode()
    fake_worker(monkeypatch, stderr=stderr, **outcome)

    with pytest.raises(PdfRejected) as caught:
        read_pdf(fx.clean_text_pdf())

    assert caught.value.status_code == 422
    assert caught.value.detail == UNREADABLE
    assert caught.value.__suppress_context__


def test_worker_stderr_never_reaches_the_response_or_the_logs(
    client, auth_headers, stub_llm, monkeypatch, captured_logs
):
    stub = stub_llm()
    headers = auth_headers()
    fake_worker(monkeypatch, returncode=1, stderr=f"Traceback: {fx.LAST_NAME}".encode())

    response = upload(client, headers, fx.clean_text_pdf())

    assert response.status_code == 422
    assert stub.calls == []
    assert fx.LAST_NAME not in response.text
    output = captured_logs()
    assert '"event": "request"' in output
    assert fx.LAST_NAME not in output
    assert "Traceback" not in output


def test_worker_runs_from_the_package_location_with_the_fixed_timeout(monkeypatch):
    data = fx.clean_text_pdf()
    calls = fake_worker(monkeypatch, stdout=b'{"pages": ["x"]}')

    read_pdf(data)

    call = calls[0]
    assert call["command"][:3] == [sys.executable, "-m", "app.pdf_worker"]
    assert call["input"] == data
    assert call["capture_output"] is True
    assert settings.pdf_parse_timeout_seconds == 30
    assert call["timeout"] == 30
    assert (Path(call["cwd"]) / "app" / "pdf_worker.py").is_file()


def test_worker_works_from_any_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert read_pdf(fx.clean_text_pdf()).text_pages == {1}


def test_each_page_is_closed_before_the_next_is_read(monkeypatch):
    events = []
    read, close = pdfplumber.page.Page.extract_text, pdfplumber.page.Page.close

    def spy_read(page, *args, **kwargs):
        events.append(("read", page.page_number))
        return read(page, *args, **kwargs)

    def spy_close(page):
        events.append(("closed", page.page_number))
        close(page)

    monkeypatch.setattr(pdfplumber.page.Page, "extract_text", spy_read)
    monkeypatch.setattr(pdfplumber.page.Page, "close", spy_close)
    pdf_worker.parse(fx.many_pages_pdf(2), 100)
    assert events[:4] == [("read", 1), ("closed", 1), ("read", 2), ("closed", 2)]


def test_worker_sets_limits_where_the_platform_has_them(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        RLIMIT_AS="as", RLIMIT_CPU="cpu", setrlimit=lambda *args: calls.append(args)
    )
    monkeypatch.setattr(pdf_worker, "resource", fake)
    pdf_worker.apply_limits()
    assert calls == [("as", (1024**3, 1024**3)), ("cpu", (30, 30))]

    monkeypatch.setattr(pdf_worker, "resource", None)
    pdf_worker.apply_limits()  # Windows dev has no resource module
    assert len(calls) == 2


def test_running_out_of_memory_is_not_treated_as_one_bad_page(monkeypatch):
    def explode(*_args, **_kwargs):
        raise MemoryError

    monkeypatch.setattr(pdfplumber.page.Page, "extract_text", explode)
    with pytest.raises(MemoryError):
        pdf_worker.parse(fx.clean_text_pdf(), 100)


def test_a_page_with_an_absurd_amount_of_text_has_no_text_layer():
    long_page = ["x" * 2_000] * 60
    report = pdf_worker.parse(fx.text_pdf([long_page, ["Enough typed text for a text layer"]]), 100)
    assert report["pages"][0] == ""
    assert report["pages"][1]


def test_the_worker_gets_none_of_the_api_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setenv("JWT_SECRET", "not-a-real-secret")
    monkeypatch.setenv("SIGNUP_CODE", "not-a-real-code")
    calls = fake_worker(monkeypatch, stdout=b'{"pages": [""]}')

    read_pdf(fx.clean_text_pdf())

    passed = calls[0]["env"]
    assert not {"ANTHROPIC_API_KEY", "JWT_SECRET", "SIGNUP_CODE"} & set(passed)
