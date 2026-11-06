"""Does the document say what the model says it says? Pure functions, no I/O.

`pages` holds each PDF page's text in order. `Evidenced.page` and `text_pages` are 1-based PDF page
numbers, so a page's text is `pages[page - 1]`.
"""

import re
import unicodedata

from app.extraction_types import FIELD_NAMES, Evidenced, ExtractionResult, Verification
from app.validators import is_placeholder

CURLY_QUOTES = str.maketrans("‘’‚‛“”„‟", "''''\"\"\"\"")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(CURLY_QUOTES).lower()
    return " ".join(text.split())


def _value_in_quote(value: str, quote: str) -> bool:
    """Whole value only: `2/17/1961` must not pass on `12/17/1961`, nor `Jan` on `Jane`."""
    digits = re.sub(r"[\s-]", "", value)
    if re.fullmatch(r"[0-9]{10}", digits):
        # NPI-shaped. Dates can't reach this branch, so they still compare as printed.
        return digits in re.sub(r"[^0-9]", "", quote)
    return re.search(rf"(?<![^\W_]){re.escape(value)}(?![^\W_])", quote) is not None


def verify_quote(field: Evidenced, pages: list[str], text_pages: set[int]) -> Verification:
    if is_placeholder(field.value):
        return Verification.not_applicable
    quote = _normalize(field.source_quote or "")
    if not quote or field.page is None or not 1 <= field.page <= len(pages):
        return Verification.failed
    if field.page not in text_pages:
        return Verification.unverified
    on_page = quote in _normalize(pages[field.page - 1])
    if on_page and _value_in_quote(_normalize(field.value), quote):
        return Verification.verified
    return Verification.failed


def quote_problems(result: ExtractionResult, pages: list[str], text_pages: set[int]) -> list[str]:
    """What a retry could fix, by field name only. A `failed` quote is exactly that set.

    Bad NPIs and dates are absent on purpose: a retry would press the model to invent a passing
    value.
    """
    return [
        f"{name}: give a source_quote copied exactly from the cited PDF page, containing the value"
        for name in FIELD_NAMES
        if verify_quote(getattr(result, name), pages, text_pages) == Verification.failed
    ]
