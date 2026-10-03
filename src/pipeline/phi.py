"""PHI protection primitives (Task 3).

Raw PHI exists only in the raw layer; everything downstream uses what this
module produces: age bands, 3-digit ZIPs, and a pseudonymous patient_key.

patient_key design: HMAC-SHA256 with a secret from the environment, truncated
to 128 bits (32 hex chars). Two input namespaces, domain-separated so they can
never collide:

  person:<last>|<first>|<dob>|<sex>   when the minimum linkage rule applies —
      the SAME person at different systems hashes to the SAME key, which is
      exactly the brief's cross-system linkage, with no match table needed.
  identity:<system>:<mrn>             when DOB is missing: never linked across
      systems (brief rule), stable within its system.

Re-runs are deterministic: same inputs + same secret => same keys.
"""

from __future__ import annotations

import hmac
import hashlib
import re
from datetime import date

_NON_ALPHA_RE = re.compile(r"[^A-Z]")
_ZIP_RE = re.compile(r"^(\d{5})(-\d{4})?$")

AGE_BANDS = ("0-17", "18-39", "40-64", "65+")


def normalize_name_part(raw: str | None) -> str | None:
    """Uppercase and strip everything non-alphabetic: 'Van Dyke' -> VANDYKE."""
    if raw is None:
        return None
    cleaned = _NON_ALPHA_RE.sub("", raw.upper())
    return cleaned or None


def first_given_name(raw: str | None) -> str | None:
    """First whitespace token only, dropping middle names/initials: 'Noah J' -> NOAH."""
    if raw is None or not raw.strip():
        return None
    return normalize_name_part(raw.strip().split()[0])


def match_key(
    last_name: str | None, first_name: str | None, dob: date | None, sex: str | None
) -> str | None:
    """The brief's minimum linkage rule. None when any component is missing
    (identities without a DOB are not linked)."""
    last = normalize_name_part(last_name)
    first = first_given_name(first_name)
    if not last or not first or dob is None or sex not in ("M", "F"):
        return None
    return f"{last}|{first}|{dob.isoformat()}|{sex}"


def patient_key(secret: str, *, match: str | None, source_system: str, mrn: str | None) -> str | None:
    """Pseudonymous key: person-scoped when linkable, identity-scoped otherwise."""
    if not secret:
        raise ValueError("PATIENT_KEY_SECRET is not set; refusing to pseudonymise")
    if match:
        payload = "person:" + match
    else:
        if mrn is None or not mrn.strip():
            return None
        payload = f"identity:{source_system}:{mrn.strip().upper()}"
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def age_band_at(dob: date | None, admit: date | None) -> str:
    if dob is None or admit is None:
        return "UNKNOWN"
    years = admit.year - dob.year - ((admit.month, admit.day) < (dob.month, dob.day))
    if years < 0:
        return "UNKNOWN"
    if years <= 17:
        return "0-17"
    if years <= 39:
        return "18-39"
    if years <= 64:
        return "40-64"
    return "65+"


def zip3(raw: str | None) -> tuple[str | None, str | None]:
    if raw is None or not raw.strip():
        return None, "MISSING"
    m = _ZIP_RE.match(raw.strip())
    if not m:
        return None, "INVALID_ZIP"
    return m.group(1)[:3], None
