"""billed_amount parsing: exact decimal USD, never floats, never silent zeros."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

TWO_PLACES = Decimal("0.01")

_SENTINELS_NOT_APPLICABLE = {"N/A", "NA", "NULL", "NONE", "-"}
_SENTINEL_PENDING = "PENDING"

_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")


def parse_amount(raw: str | None, amount_unit: str) -> tuple[Decimal | None, str | None]:
    """Parse a raw billed amount into USD with two decimal places.

    amount_unit is the system's documented unit: "USD" or "USD_CENTS"
    (LEGACY_MEDITECH reports integer cents, e.g. 1845000 -> 18450.00).
    Handles currency symbols/codes, thousands separators, a K suffix and
    stray whitespace. Missing or unparseable values are (None, reason) —
    never zero.
    """
    if raw is None or not raw.strip():
        return None, "MISSING"
    text = raw.strip().upper()
    if text in _SENTINELS_NOT_APPLICABLE:
        return None, "NOT_APPLICABLE"
    if text == _SENTINEL_PENDING:
        return None, "PENDING"
    if text.startswith("#"):  # spreadsheet error artefacts like #REF!, #DIV/0!
        return None, "SPREADSHEET_ERROR"

    cleaned = (
        text.replace("USD", "")
        .replace("$", "")
        .replace(",", "")
        .replace(" ", "")
    )
    multiplier = Decimal(1)
    if cleaned.endswith("K"):
        multiplier = Decimal(1000)
        cleaned = cleaned[:-1]

    if not _NUMERIC_RE.match(cleaned):
        return None, "UNPARSEABLE_AMOUNT"
    try:
        value = Decimal(cleaned) * multiplier
    except InvalidOperation:
        return None, "UNPARSEABLE_AMOUNT"

    if amount_unit == "USD_CENTS":
        value = value / Decimal(100)
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP), None
