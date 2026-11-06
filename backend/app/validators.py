"""Value checks. Pure functions: they report on a value and never change it."""

import re
import unicodedata
from datetime import date, datetime

from app.extraction_types import FlagCode

MAX_AGE_YEARS = 120
NPI_PREFIX = "80840"  # the health-industry prefix the NPI check digit is computed over
TWO_DIGIT_YEAR_FORMAT = "%m/%d/%y"
DOB_FORMATS = ("%m/%d/%Y", "%m-%d-%Y", TWO_DIGIT_YEAR_FORMAT, "%Y-%m-%d", "%B %d, %Y")
# Compared with everything but letters and digits removed, so "N / A" and "[unknown]" match too. A
# false match only sends the Order to a person with the printed text still in evidence, so "na"
# stays in. A false miss approves a stand-in as somebody's name.
PLACEHOLDERS = frozenset(
    {
        "blank",
        "declined",
        "illegible",
        "na",
        "nil",
        "none",
        "notapplicable",
        "notavailable",
        "notprovided",
        "null",
        "patient",
        "pending",
        "refused",
        "same",
        "tba",
        "tbd",
        "unavailable",
        "unk",
        "unknown",
    }
)
PLACEHOLDER_PREFIXES = ("see ", "same as", "not ", "no ", "unknown ")


def _luhn_passes(digits: str) -> bool:
    total = 0
    for position, digit in enumerate(reversed(digits)):
        number = int(digit)
        if position % 2 == 1:
            number = number * 2 - 9 if number > 4 else number * 2
        total += number
    return total % 10 == 0


def is_valid_npi(value: str) -> bool:
    """Ten digits from 1 or 2, passing Luhn with the prefix. Spaces and hyphens don't count."""
    digits = re.sub(r"[\s-]", "", value)
    return re.fullmatch(r"[12][0-9]{9}", digits) is not None and _luhn_passes(NPI_PREFIX + digits)


def parse_dob(value: str, today: date) -> date | None:
    """Month first. `today` places two-digit years: one landing after it moves back a century."""
    text = value.strip()
    for date_format in DOB_FORMATS:
        try:
            parsed = datetime.strptime(text, date_format).date()
            if date_format == TWO_DIGIT_YEAR_FORMAT and parsed > today:
                parsed = parsed.replace(year=parsed.year - 100)
        except ValueError:
            continue
        return parsed
    return None


def has_two_digit_year(value: str) -> bool:
    """True when `parse_dob` had to guess the century."""
    try:
        datetime.strptime(value.strip(), TWO_DIGIT_YEAR_FORMAT)
    except ValueError:
        return False
    return True


def check_dob(value: date, today: date) -> FlagCode | None:
    # Tuples, because subtracting years from Feb 29 can land on a date that doesn't exist.
    cutoff_passed = (value.year + MAX_AGE_YEARS, value.month, value.day) < (
        today.year,
        today.month,
        today.day,
    )
    return FlagCode.implausible_dob if value > today or cutoff_passed else None


def is_placeholder(value: str | None) -> bool:
    """True for no value, for printed stand-ins like N/A, and for text with nothing readable."""
    if value is None:
        return True
    text = unicodedata.normalize("NFKC", value).casefold()
    compact = re.sub(r"[\W_]", "", text)
    if len(set(compact)) <= 1:  # nothing readable, a lone initial, or a row of x's
        return True
    spaced = " ".join(re.sub(r"[\W_]", " ", text).split())
    return compact in PLACEHOLDERS or spaced.startswith(PLACEHOLDER_PREFIXES)
