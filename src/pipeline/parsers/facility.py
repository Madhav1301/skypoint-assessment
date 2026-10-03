"""Facility resolution: normalised exact lookup against the curated alias table.

Deliberately deterministic — no fuzzy scores. With 8 facilities and ~35
observed spellings an alias table is auditable and cannot silently mis-assign
a hospital; unknown names quarantine (UNRESOLVED_FACILITY) and new variants
are added to config/facility_aliases.yaml after review.

Normalisation (identical for config keys and incoming values):
uppercase; drop . and ' ; turn - and , into spaces; collapse whitespace.
"""

from __future__ import annotations

import re

_DROP_RE = re.compile(r"[.']")
_SPACE_RE = re.compile(r"[-,]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_facility_name(raw: str) -> str:
    text = raw.strip().upper()
    text = _DROP_RE.sub("", text)
    text = _SPACE_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def build_alias_lookup(aliases_cfg: dict) -> dict[str, dict[str, str]]:
    """{source_system: {normalised_variant: facility_id}} from the YAML config."""
    lookup: dict[str, dict[str, str]] = {}
    for source_system, by_facility in aliases_cfg.items():
        table: dict[str, str] = {}
        for facility_id, variants in by_facility.items():
            for variant in variants:
                table[normalize_facility_name(str(variant))] = facility_id
        lookup[source_system] = table
    return lookup


def resolve_facility(
    raw: str | None, source_system: str, alias_lookup: dict[str, dict[str, str]]
) -> tuple[str | None, str | None]:
    if raw is None or not raw.strip():
        return None, "MISSING"
    facility_id = alias_lookup.get(source_system, {}).get(normalize_facility_name(raw))
    if facility_id is None:
        return None, "UNRESOLVED_FACILITY"
    return facility_id, None
