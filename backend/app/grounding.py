"""Does the document say what the model says it says? Pure functions, no I/O.

`pages` holds each PDF page's text in order. `Evidenced.page` and `text_pages` are 1-based PDF page
numbers, so a page's text is `pages[page - 1]`.
"""

import re
import unicodedata

from app.extraction_types import FIELD_NAMES, Evidenced, ExtractionResult, Verification
from app.validators import is_placeholder

CURLY_QUOTES = str.maketrans("‘’‚‛“”„‟", "''''\"\"\"\"")
# A letter or digit, or something that joins them: `Smith` is only part of `Smith-Jones`.
WORD_PART = r"(?:[^\W_]|[-'\u0300-\u036f])"


def _normalize(text: str) -> str:
    # Format characters first: invisible, so `Jan<ZWSP>e` is the word `Jane`, not `Jan` and `e`.
    text = "".join(character for character in text if unicodedata.category(character) != "Cf")
    text = unicodedata.normalize("NFKC", text).translate(CURLY_QUOTES).lower()
    return " ".join(text.split())


def _contains_whole(text: str, part: str) -> bool:
    """`2/17/1961` must not pass on `12/17/1961`, nor `Jan` on `Jane`."""
    return re.search(rf"(?<!{WORD_PART}){re.escape(part)}(?!{WORD_PART})", text) is not None


def _value_in_quote(value: str, quote: str, name: str | None) -> bool:
    digits = re.sub(r"[\s-]", "", value)
    if name == "provider_npi" and re.fullmatch(r"[0-9]{10}", digits):
        # NPIs print grouped. Every other field compares as printed, so nothing can be spliced
        # together from the digits of a date or phone number.
        grouped = r"[\s-]*".join(digits)
        return re.search(rf"(?<![0-9]){grouped}(?![0-9])", quote) is not None
    return _contains_whole(quote, value)


def verify_quote(
    field: Evidenced, pages: list[str], text_pages: set[int], name: str | None = None
) -> Verification:
    if is_placeholder(field.value):
        return Verification.not_applicable
    quote = _normalize(field.source_quote or "")
    if not quote or field.page is None or not 1 <= field.page <= len(pages):
        return Verification.failed
    if field.page not in text_pages:
        return Verification.unverified
    on_page = _contains_whole(_normalize(pages[field.page - 1]), quote)
    if on_page and _value_in_quote(_normalize(field.value), quote, name):
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
        if verify_quote(getattr(result, name), pages, text_pages, name) == Verification.failed
    ]
