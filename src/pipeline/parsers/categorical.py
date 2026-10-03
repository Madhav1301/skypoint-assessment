"""Config-driven categorical mappings (encounter type, claim status, payer, sex).

Mappings live in config/value_mappings.yaml as canonical -> [variants]; this
module inverts them into a normalised lookup. Matching normalisation:
uppercase, trim, collapse internal whitespace.
"""

from __future__ import annotations

import re

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_token(raw: str) -> str:
    return _WHITESPACE_RE.sub(" ", raw.strip().upper())


def build_lookup(mapping: dict[str, list[str]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canonical, variants in mapping.items():
        for variant in variants:
            lookup[normalize_token(str(variant))] = canonical
    return lookup


def map_encounter_type(raw: str | None, lookup: dict[str, str]) -> tuple[str, str | None]:
    """Always yields a canonical value; unmapped input becomes UNKNOWN + flag."""
    if raw is None or not raw.strip():
        return "UNKNOWN", "MISSING"
    canonical = lookup.get(normalize_token(raw))
    if canonical is None:
        return "UNKNOWN", "UNMAPPED_ENCOUNTER_TYPE"
    return canonical, None


def map_claim_status(raw: str | None, lookup: dict[str, str]) -> tuple[str | None, str | None]:
    """Claim status has no UNKNOWN bucket: unmapped stays null with a reason,
    and export filters later treat null as failing (fail-closed)."""
    if raw is None or not raw.strip():
        return None, "MISSING"
    canonical = lookup.get(normalize_token(raw))
    if canonical is None:
        return None, "UNMAPPED_CLAIM_STATUS"
    return canonical, None


def map_payer_category(raw: str | None, lookup: dict[str, str]) -> tuple[str, str | None]:
    if raw is None or not raw.strip():
        return "UNKNOWN", "MISSING"
    canonical = lookup.get(normalize_token(raw))
    if canonical is None:
        return "UNKNOWN", "UNMAPPED_PAYER"
    return canonical, None


def map_sex(raw: str | None, lookup: dict[str, str]) -> tuple[str, str | None]:
    if raw is None or not raw.strip():
        return "UNKNOWN", "MISSING"
    canonical = lookup.get(normalize_token(raw))
    if canonical is None:
        return "UNKNOWN", "UNMAPPED_SEX"
    return canonical, None
