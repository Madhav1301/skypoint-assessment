"""Provider dimension as a Type 2 slowly changing dimension (Task 5).

Built from the roster snapshots. Validity semantics from the brief:
a snapshot's values hold from its as_of_date until the next snapshot, and
the earliest snapshot also applies to all earlier dates — so providers in
the earliest snapshot are back-dated to 1900-01-01. A provider who joins in
a later snapshot starts at that snapshot; one who disappears has their last
period closed at the snapshot they vanish from. Consecutive snapshots with
identical attributes collapse into one period, so an unchanged provider has
exactly one row. valid_to is EXCLUSIVE; the open period ends 9999-12-31.

Point-in-time join: admit_date >= valid_from AND admit_date < valid_to.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

BACKDATED_FROM = date(1900, 1, 1)
OPEN_ENDED_TO = date(9999, 12, 31)

_ROSTER_NAME_RE = re.compile(r"^roster_(\d{4}-\d{2}-\d{2})\.csv$")

ATTRIBUTES = (
    "provider_last_name", "provider_first_name", "credential",
    "specialty", "primary_facility_id", "employment_status",
)


@dataclass(frozen=True)
class ProviderPeriod:
    npi: str
    provider_last_name: str
    provider_first_name: str
    credential: str
    specialty: str
    primary_facility_id: str
    employment_status: str
    valid_from: date
    valid_to: date  # exclusive

    @property
    def is_current(self) -> bool:
        return self.valid_to == OPEN_ENDED_TO


def load_roster_snapshots(roster_dir: Path) -> list[tuple[date, dict[str, dict]]]:
    """[(as_of_date, {npi: attribute_row})], sorted by date."""
    snapshots = []
    for path in sorted(roster_dir.glob("roster_*.csv")):
        m = _ROSTER_NAME_RE.match(path.name)
        if not m:
            continue
        as_of = date.fromisoformat(m.group(1))
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            rows = {
                row["npi"].strip(): {attr: row[attr].strip() for attr in ATTRIBUTES}
                for row in csv.DictReader(f)
            }
        snapshots.append((as_of, rows))
    return snapshots


def build_provider_scd2(snapshots: list[tuple[date, dict[str, dict]]]) -> list[ProviderPeriod]:
    if not snapshots:
        return []
    earliest = snapshots[0][0]
    all_npis = sorted({npi for _, rows in snapshots for npi in rows})
    periods: list[ProviderPeriod] = []

    for npi in all_npis:
        open_attrs: dict | None = None
        open_from: date | None = None
        for as_of, rows in snapshots:
            attrs = rows.get(npi)
            if attrs is None:
                if open_attrs is not None:  # dropped from the roster
                    periods.append(_period(npi, open_attrs, open_from, as_of))
                    open_attrs, open_from = None, None
                continue
            if open_attrs is None:  # first appearance (or rejoining)
                open_from = BACKDATED_FROM if as_of == earliest else as_of
                open_attrs = attrs
            elif attrs != open_attrs:  # attribute change -> close and reopen
                periods.append(_period(npi, open_attrs, open_from, as_of))
                open_attrs, open_from = attrs, as_of
            # identical attrs: period simply continues (collapse)
        if open_attrs is not None:
            periods.append(_period(npi, open_attrs, open_from, OPEN_ENDED_TO))

    return periods


def _period(npi: str, attrs: dict, valid_from: date, valid_to: date) -> ProviderPeriod:
    return ProviderPeriod(npi=npi, valid_from=valid_from, valid_to=valid_to, **attrs)
