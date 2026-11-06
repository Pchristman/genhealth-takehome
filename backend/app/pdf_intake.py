"""Refuse a bad file before anything is spent on it, and read each page's text layer."""

from dataclasses import dataclass
from io import BytesIO

import pdfplumber
from fastapi import status

from app.config import settings

MIN_TEXT_CHARS = 25  # non-space characters a page needs before it counts as having a text layer
UNREADABLE = "The PDF is encrypted or corrupt and could not be read."


class PdfRejected(Exception):
    """Details are fixed text. A parser's own message can quote the document."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class ParsedPdf:
    pages: list[str]  # by position, so PDF page n is pages[n - 1]
    text_pages: set[int]  # 1-based PDF page numbers
    page_count: int


def _has_text_layer(text: str) -> bool:
    return len("".join(text.split())) >= MIN_TEXT_CHARS


def _page_texts(data: bytes) -> list[str]:
    with pdfplumber.open(BytesIO(data)) as pdf:
        # A blank user password opens without one, but such a file is still encrypted.
        if pdf.doc.encryption or not pdf.pages:
            raise PdfRejected(status.HTTP_422_UNPROCESSABLE_CONTENT, UNREADABLE)
        if len(pdf.pages) > settings.upload_max_pages:
            raise PdfRejected(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"The PDF is over the {settings.upload_max_pages} page limit.",
            )
        return [page.extract_text() or "" for page in pdf.pages]


def read_pdf(data: bytes) -> ParsedPdf:
    """Size, then magic bytes, then parse, then page count. Nothing past here trusts the client."""
    if len(data) > settings.upload_max_bytes:
        raise PdfRejected(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"The file is over the {settings.upload_max_bytes} byte limit.",
        )
    if not data.startswith(b"%PDF-"):
        raise PdfRejected(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "The file is not a PDF.")
    try:
        pages = _page_texts(data)
    except PdfRejected:
        raise
    except Exception:
        # Any file can reach this parser, so any failure is the file's fault. Its exception text
        # may hold document content, so it is dropped here rather than logged.
        raise PdfRejected(status.HTTP_422_UNPROCESSABLE_CONTENT, UNREADABLE) from None
    text_pages = {number for number, text in enumerate(pages, start=1) if _has_text_layer(text)}
    return ParsedPdf(pages=pages, text_pages=text_pages, page_count=len(pages))
