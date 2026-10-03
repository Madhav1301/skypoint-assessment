"""Version classification (Task 4): duplicates, stale replays, new versions.

A version's identity is (source_system, source_record_id, last_updated_ts in
UTC). Comparing instants in UTC is what makes the classification immune to
the pack's mixed timestamp spellings — the batch_003 replay of encounter
139507 carries the same instant as its batch_001 original in a different
format, and must be a duplicate, not a new version.

Rules, applied in file order so outcomes are deterministic:
  duplicate -> this exact version is already held (from an earlier batch or
               earlier in this run), byte-identical or reformatted
  stale     -> older than the newest version already held for the encounter;
               skipped, so it can never overwrite current state (A4/A19 —
               equal timestamps: first arrival wins)
  new       -> inserted into the insert-only history with its arrival batch
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class VersionState:
    """Known versions, loaded once per run and updated as batches land."""

    seen: set = field(default_factory=set)          # (system, record_id, ts_utc)
    newest: dict = field(default_factory=dict)      # (system, record_id) -> ts_utc

    @classmethod
    def load(cls, con) -> "VersionState":
        state = cls()
        for system, record_id, ts in con.execute(
            "SELECT source_system, source_record_id, last_updated_ts_utc "
            "FROM clean.encounter_versions"
        ).fetchall():
            state.remember(system, record_id, ts)
        return state

    def remember(self, system: str, record_id: str, ts: datetime) -> None:
        self.seen.add((system, record_id, ts))
        key = (system, record_id)
        if key not in self.newest or ts > self.newest[key]:
            self.newest[key] = ts


@dataclass(frozen=True)
class Classification:
    outcome: str  # NEW | DUPLICATE | STALE


def classify_version(
    state: VersionState, system: str, record_id: str, ts: datetime
) -> Classification:
    if (system, record_id, ts) in state.seen:
        return Classification("DUPLICATE")
    newest = state.newest.get((system, record_id))
    if newest is not None and ts < newest:
        return Classification("STALE")
    state.remember(system, record_id, ts)
    return Classification("NEW")
