"""Schema contracts (Task 1): a file header must exactly match a configured
version for its source system; the match supplies the mapping to canonical
column names. Unknown headers stop the batch with an actionable error.
"""

from __future__ import annotations

from dataclasses import dataclass


class SchemaContractError(Exception):
    pass


@dataclass(frozen=True)
class ContractMatch:
    source_system: str
    version: str
    canonical_by_source: dict[str, str]
    extra_columns: tuple[str, ...]


def match_contract(contracts: dict, source_system: str, header: list[str]) -> ContractMatch:
    versions = contracts.get(source_system)
    if not versions:
        raise SchemaContractError(
            f"SCHEMA_CONTRACT: no contract configured for source system '{source_system}'"
        )
    for version in versions:
        if list(version["columns"]) == list(header):
            rename = version.get("rename") or {}
            extras = tuple(version.get("extras") or ())
            mapping = {
                col: rename.get(col, col) for col in header if col not in extras
            }
            return ContractMatch(
                source_system=source_system,
                version=str(version["version"]),
                canonical_by_source=mapping,
                extra_columns=extras,
            )

    closest = max(versions, key=lambda v: len(set(v["columns"]) & set(header)))
    missing = [c for c in closest["columns"] if c not in header]
    unexpected = [c for c in header if c not in closest["columns"]]
    reordered = not missing and not unexpected
    detail = "columns reordered" if reordered else f"missing={missing}, unexpected={unexpected}"
    raise SchemaContractError(
        f"SCHEMA_CONTRACT: {source_system} header matches no configured version "
        f"(known: {[str(v['version']) for v in versions]}). Closest '{closest['version']}': {detail}. "
        f"If this change is expected, add a new version to config/schema_contracts.yaml; "
        f"unknown schema changes stop the batch."
    )
