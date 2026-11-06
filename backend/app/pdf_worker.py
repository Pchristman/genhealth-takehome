"""Parse one PDF in a process of its own: PDF bytes on stdin, JSON on stdout.

A few hundred KB of PDF can inflate into gigabytes, so the parser runs where the operating system
can stop it and the API's memory is out of reach. This module reads no settings, because the API's
secrets have no business here: the page limit arrives as the one argument.

A failure is a non-zero exit. Its stderr can quote the document, so the parent never reads it.
"""

import json
import logging
import sys
from io import BytesIO
from typing import Any

import pdfplumber

try:
    import resource
except ImportError:  # Windows dev. The parent's timeout still applies.
    resource = None

MIN_TEXT_CHARS = 25  # non-space characters a page needs before it counts as having a text layer
MAX_IMAGE_COVERAGE = 0.5  # at this share of the page, it is a scan whatever text sits on it
MAX_PAGE_CHARS = 100_000  # no real page holds this much, and all of it would reach the parent
MEMORY_LIMIT_BYTES = 1024**3
CPU_LIMIT_SECONDS = 30


def apply_limits() -> None:
    if resource is None:
        return
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_SECONDS, CPU_LIMIT_SECONDS))


def _image_coverage(page: Any) -> float:
    """Share of the page under images. Overlaps count twice, which only errs towards "scan"."""
    left, top, right, bottom = page.bbox
    area = (right - left) * (bottom - top)
    covered = sum(
        max(0, min(image["x1"], right) - max(image["x0"], left))
        * max(0, min(image["bottom"], bottom) - max(image["top"], top))
        for image in page.images
    )
    return covered / area if area > 0 else 1.0


def _text_layer(page: Any) -> str:
    """The page's text, or "" unless it is a real text layer that quotes can be checked against.

    `extract_text` also returns invisible OCR text, and a page of OCR proves nothing about the image
    under it. The model reads rotated pages from the image, so their text can't confirm it either.
    """
    if page.rotation:
        return ""
    text = page.extract_text() or ""
    if not MIN_TEXT_CHARS <= len("".join(text.split())) <= MAX_PAGE_CHARS:
        return ""
    return text if _image_coverage(page) < MAX_IMAGE_COVERAGE else ""


def _page_text(page: Any) -> str:
    try:
        return _text_layer(page)
    except MemoryError:
        raise  # the file is the problem, not this page
    except Exception:
        return ""  # one bad page must not cost the whole fax, and "" only sends it to review
    finally:
        page.close()


def parse(data: bytes, max_pages: int) -> dict[str, Any]:
    """One string per page, "" for a page without a text layer."""
    with pdfplumber.open(BytesIO(data)) as pdf:
        # A blank user password opens without one, but such a file is still encrypted.
        if pdf.doc.encryption or not pdf.pages:
            raise ValueError("unreadable")
        if len(pdf.pages) > max_pages:
            return {"too_many_pages": True}
        return {"pages": [_page_text(page) for page in pdf.pages]}


def main() -> None:
    apply_limits()
    logging.disable(logging.CRITICAL)  # a hostile file can make the parser warn without end
    report = parse(sys.stdin.buffer.read(), int(sys.argv[1]))
    sys.stdout.write(json.dumps(report))


if __name__ == "__main__":
    main()
