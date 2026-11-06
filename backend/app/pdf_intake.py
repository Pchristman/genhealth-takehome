"""Refuse a bad file before anything is spent on it, and read each page's text layer.

The parsing itself happens in app.pdf_worker, in a process of its own.
"""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from fastapi import status

from app.config import settings

PACKAGE_ROOT = Path(__file__).resolve().parents[1]  # where `-m app.pdf_worker` resolves
PDF_HEADER = b"%PDF-"
HEADER_SEARCH_BYTES = 1024  # fax gateways and mailers can put bytes ahead of the header
UNREADABLE = "The PDF is encrypted or corrupt and could not be read."


class PdfRejected(Exception):
    """Details are fixed text. A parser's own message can quote the document."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class ParsedPdf:
    pages: list[str]  # by position, so PDF page n is pages[n - 1]; "" means no text layer
    text_pages: set[int]  # 1-based PDF page numbers
    page_count: int


def _page_texts(data: bytes) -> list[str]:
    finished = subprocess.run(
        [sys.executable, "-m", "app.pdf_worker", str(settings.upload_max_pages)],
        input=data,
        capture_output=True,
        timeout=settings.pdf_parse_timeout_seconds,
        cwd=PACKAGE_ROOT,
        # Windows can't start Python without SYSTEMROOT. Nothing else is passed,
        # so a parser exploit finds no API secrets in its environment.
        env={key: os.environ[key] for key in ("SYSTEMROOT",) if key in os.environ},
    )
    if finished.returncode != 0:
        raise ValueError("worker failed")  # its stderr can quote the document, so it stays unread
    report = json.loads(finished.stdout)
    if report.get("too_many_pages"):
        raise PdfRejected(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"The PDF is over the {settings.upload_max_pages} page limit.",
        )
    pages = report["pages"]
    if not pages or not all(isinstance(text, str) for text in pages):
        raise ValueError("bad worker output")
    return pages


def read_pdf(data: bytes) -> ParsedPdf:
    """Size, then magic bytes, then parse, then page count. Nothing past here trusts the client."""
    if len(data) > settings.upload_max_bytes:
        raise PdfRejected(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"The file is over the {settings.upload_max_bytes} byte limit.",
        )
    if PDF_HEADER not in data[:HEADER_SEARCH_BYTES]:
        raise PdfRejected(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "The file is not a PDF.")
    try:
        pages = _page_texts(data)
    except PdfRejected:
        raise
    except Exception:
        # Any file can reach the parser, so any failure is the file's fault. Timeouts and bad
        # output land here too, and their text may hold document content: dropped, never logged.
        raise PdfRejected(status.HTTP_422_UNPROCESSABLE_CONTENT, UNREADABLE) from None
    text_pages = {number for number, text in enumerate(pages, start=1) if text}
    return ParsedPdf(pages=pages, text_pages=text_pages, page_count=len(pages))
