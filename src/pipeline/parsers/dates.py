"""Date and timestamp parsing.

Dates: ISO, ISO-with-time, month names, and numeric forms with / or -.
Ambiguous numeric dates are read with the SYSTEM's documented day/month
order (05/03/2024 is 3 May for an MDY system, 5 March for a DMY system).
Two-digit years mean 20xx, per the source metadata.

Timestamps: an explicit UTC offset in the value always wins; otherwise the
value is wall-clock time in the system's documented time zone. Everything is
returned in UTC, because version ordering compares absolute instants.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

_SENTINELS = {"TBD", "N/A", "NA", "UNKNOWN", "NULL", "NONE", "PENDING", "-"}

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_ISO_DATETIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ]\d{2}:\d{2}:\d{2}")
_NUMERIC_DATE_RE = re.compile(r"^(\d{1,4})[/-](\d{1,2})[/-](\d{1,4})$")
_MONTH_NAME_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y")

_NAIVE_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


def _build_date(year: int, month: int, day: int) -> tuple[date | None, str | None]:
    if year < 100:  # two-digit year: metadata says 20xx
        year += 2000
    if year < 1900:
        return None, "INVALID_DATE"
    try:
        return date(year, month, day), None
    except ValueError:  # impossible calendar date, e.g. 2024-02-30
        return None, "INVALID_DATE"


def parse_date(raw: str | None, date_order: str) -> tuple[date | None, str | None]:
    """Parse one raw date using the system's date order ("MDY" or "DMY")."""
    if raw is None or not raw.strip():
        return None, "MISSING"
    text = raw.strip()
    if text.upper() in _SENTINELS:
        return None, "NOT_PROVIDED"

    m = _ISO_DATE_RE.match(text) or _ISO_DATETIME_RE.match(text)
    if m:
        return _build_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    m = _NUMERIC_DATE_RE.match(text)
    if m:
        a, b, c = (int(m.group(i)) for i in (1, 2, 3))
        if len(m.group(1)) == 4:  # unambiguous year-first
            return _build_date(a, b, c)
        if date_order == "DMY":
            return _build_date(c, b, a)
        return _build_date(c, a, b)  # MDY

    for fmt in _MONTH_NAME_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
            return _build_date(parsed.year, parsed.month, parsed.day)
        except ValueError:
            continue

    return None, "UNPARSEABLE_DATE"


def check_not_after(value: date | None, limit: date) -> str | None:
    """FUTURE_DATE when a parsed date lies after the batch's delivery date."""
    if value is not None and value > limit:
        return "FUTURE_DATE"
    return None


def parse_timestamp_utc(
    raw: str | None, default_timezone: str, date_order: str
) -> tuple[datetime | None, str | None]:
    """Parse last_updated_ts to an aware UTC datetime.

    Accepts ISO with Z or an explicit offset (offset wins), ISO/space-separated
    naive timestamps (interpreted in default_timezone), and numeric-date
    wall-clock forms like 26/10/2024 07:32:41 read with the system date order.
    For wall-clock times made ambiguous by a DST fall-back, fold=1 resolves to
    the LATER (standard-time) reading — documented assumption A11.
    """
    if raw is None or not raw.strip():
        return None, "MISSING"
    text = raw.strip()

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc), None
        return _localize(parsed, default_timezone)
    except ValueError:
        pass

    for fmt in _NAIVE_TS_FORMATS:
        try:
            return _localize(datetime.strptime(text, fmt), default_timezone)
        except ValueError:
            continue

    numeric_fmt = "%d/%m/%Y %H:%M:%S" if date_order == "DMY" else "%m/%d/%Y %H:%M:%S"
    try:
        return _localize(datetime.strptime(text, numeric_fmt), default_timezone)
    except ValueError:
        return None, "UNPARSEABLE_TIMESTAMP"


def _localize(naive: datetime, tz_name: str) -> tuple[datetime, None]:
    aware = naive.replace(tzinfo=ZoneInfo(tz_name), fold=1)
    return aware.astimezone(timezone.utc), None


def derive_period(value: date) -> tuple[int, int, int]:
    """(year, quarter, month) for reporting, derived from admit_date."""
    return value.year, (value.month - 1) // 3 + 1, value.month
