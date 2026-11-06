import json

import pytest
from reportlab.lib.pdfencrypt import StandardEncryption
from sqlmodel import select

from app.config import settings
from app.models import Document
from app.pdf_intake import ParsedPdf, PdfRejected, read_pdf
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


@pytest.mark.parametrize("error", [RuntimeError, RecursionError, KeyError, MemoryError])
def test_any_parser_failure_is_a_422_that_carries_no_text(monkeypatch, error):
    def explode(*_args, **_kwargs):
        raise error(fx.LAST_NAME)

    monkeypatch.setattr("app.pdf_intake.pdfplumber.open", explode)
    with pytest.raises(PdfRejected) as caught:
        read_pdf(fx.clean_text_pdf())
    assert caught.value.status_code == 422
    assert fx.LAST_NAME not in caught.value.detail
    assert caught.value.__suppress_context__


def test_hostile_pdf_is_a_422_not_a_500_and_logs_no_text(
    client, auth_headers, stub_llm, monkeypatch, captured_logs
):
    stub = stub_llm()

    def explode(*_args, **_kwargs):
        raise ValueError(fx.LAST_NAME)

    monkeypatch.setattr("app.pdf_intake.pdfplumber.open", explode)
    response = upload(client, auth_headers(), fx.clean_text_pdf())

    assert response.status_code == 422
    assert stub.calls == []
    assert fx.LAST_NAME not in response.text
    output = captured_logs()
    assert '"event": "request"' in output
    assert fx.LAST_NAME not in output


def test_a_pdf_with_no_pages_is_a_422(monkeypatch):
    class Empty:
        pages: list = []
        doc = type("Doc", (), {"encryption": None})

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr("app.pdf_intake.pdfplumber.open", lambda *_a, **_k: Empty())
    with pytest.raises(PdfRejected) as caught:
        read_pdf(fx.clean_text_pdf())
    assert caught.value.status_code == 422


def test_rejection_details_are_json_safe_static_text():
    with pytest.raises(PdfRejected) as caught:
        read_pdf(b"not a pdf at all")
    assert json.dumps({"detail": caught.value.detail})
    assert "not a pdf at all" not in caught.value.detail
