"""Diagnosis-code normalisation and four-way classification.

Outcomes (the brief's exact taxonomy):
  IN_REFERENCE                   normalised ICD-10 found in the reference file
  VALID_FORMAT_NOT_IN_REFERENCE  well-formed ICD-10, absent from the reference
  ICD9_LEGACY                    a legacy ICD-9 code; never mapped to ICD-10
  UNPARSEABLE                    nothing recognisable (includes sentinels)
  MISSING                        blank

Normalisation: uppercase, trim, strip a trailing description after " - ",
insert the dot after the third character when absent (E1165 -> E11.65).

Judgment call: only purely numeric codes (496, 401.9, 250.00) are classified
as ICD-9. E-prefixed values like E119 are read as dotless ICD-10 (E11.9,
diabetes) rather than ICD-9 external-cause codes — in this network's feeds
every observed E-code is an ICD-10 diabetes/lipid code, and the reference
contains them. Documented in the assumptions log.
"""

from __future__ import annotations

import re

_SENTINELS = {"UNKNOWN", "N/A", "NA", "NULL", "NONE", "TBD", "-"}

_ICD9_NUMERIC_RE = re.compile(r"^\d{3}(\.\d{1,2})?$")
_ICD10_RE = re.compile(r"^[A-Z]\d[0-9A-Z](\.[0-9A-Z]{1,4})?$")


def normalize_dx(raw: str | None, reference_codes: set[str]) -> tuple[str | None, str]:
    """Returns (normalised_code_or_None, outcome).

    The code is populated only for the two ICD-10 outcomes; for ICD9_LEGACY,
    UNPARSEABLE and MISSING it is None and the raw value remains the record.
    """
    if raw is None or not raw.strip():
        return None, "MISSING"
    text = raw.strip().upper()
    if text in _SENTINELS:
        return None, "UNPARSEABLE"

    candidate = text.split(" - ")[0].strip()

    if _ICD9_NUMERIC_RE.match(candidate):
        return None, "ICD9_LEGACY"

    if "." not in candidate and len(candidate) > 3:
        candidate = candidate[:3] + "." + candidate[3:]

    if not _ICD10_RE.match(candidate):
        return None, "UNPARSEABLE"
    if candidate in reference_codes:
        return candidate, "IN_REFERENCE"
    return candidate, "VALID_FORMAT_NOT_IN_REFERENCE"
