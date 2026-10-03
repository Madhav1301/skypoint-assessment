"""NPI cleaning and validation.

An NPI is 10 digits whose last digit is a Luhn check digit computed over
'80840' + the first 9 digits, as CMS defines it. Harmless formatting (Excel
float artefacts like '1608565176.0', spaces, hyphens, an NPI- prefix) is
cleaned first; anything that is not 10 digits afterwards is INVALID_FORMAT.
Whether a structurally valid NPI exists in a roster is a separate, later
check (NPI_NOT_IN_ROSTER) — the brief requires distinguishing the two.
"""

from __future__ import annotations

import re

_SENTINELS = {"N/A", "NA", "NULL", "NONE", "UNKNOWN", "-"}
_FLOAT_ARTIFACT_RE = re.compile(r"^(\d+)\.0+$")


def luhn_80840_valid(npi10: str) -> bool:
    digits = "80840" + npi10[:9]
    total = 0
    double = True
    for ch in reversed(digits):
        d = int(ch)
        if double:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        double = not double
    return (10 - total % 10) % 10 == int(npi10[9])


def clean_npi(raw: str | None) -> tuple[str | None, str | None]:
    if raw is None or not raw.strip():
        return None, "MISSING"
    text = raw.strip()
    if text.upper() in _SENTINELS:
        return None, "NOT_PROVIDED"

    m = _FLOAT_ARTIFACT_RE.match(text)
    if m:
        text = m.group(1)
    digits = re.sub(r"\D", "", text)

    if len(digits) != 10:
        return None, "INVALID_NPI_FORMAT"
    if not luhn_80840_valid(digits):
        return None, "INVALID_NPI_LUHN"
    return digits, None
