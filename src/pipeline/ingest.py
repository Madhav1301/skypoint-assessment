"""Batch processing (Tasks 1, 4 and 6): manifest gate, schema contracts,
cleaning, version classification, publish gate, and one atomic write.

A batch is all-or-nothing, implemented as nothing-until-decided: every file
is validated, read and cleaned in memory, versions are classified and the
publish gate is evaluated BEFORE the single write transaction begins. A
rejected or gate-failed batch therefore leaves exactly two traces — batch
audit rows and dq_results — and no change to raw, clean or quarantine.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from . import logging_setup as log
from .bulk import bulk_insert
from .cleaning import CleanContext, CleanedRow, build_context, clean_row
from .db import CANONICAL_RAW_FIELDS, CLEAN_COLUMNS, QUARANTINE_COLUMNS, RAW_COLUMNS
from .manifest import Manifest, ManifestError, load_manifest, sha256_of_file, validate_batch_files
from .quality import aggregate_dq, gate_passes
from .schema_contract import ContractMatch, SchemaContractError, match_contract
from .versioning import VersionState, classify_version

CANONICAL_COLUMNS = CANONICAL_RAW_FIELDS  # re-export; tests and callers use this name

_AUDIT_SQL = """
    INSERT INTO meta.batch_audit (batch_id, file_name, source_system, status, reason,
        rows_received, rows_loaded, rows_duplicate, rows_stale, rows_quarantined,
        delivered_at, started_at, finished_at, duration_ms)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class LoadError(Exception):
    pass


@dataclass(frozen=True)
class RunConfig:
    """Everything a batch needs, assembled once per run (see run.build_run_config)."""

    contracts: dict
    conventions: dict
    facility_aliases_cfg: dict
    value_mappings_cfg: dict
    icd10_codes: set
    patient_key_secret: str
    gate_threshold: float


@dataclass
class FileResult:
    file_name: str
    source_system: str
    sha256: str
    contract: ContractMatch
    raw_rows: list[tuple] = field(default_factory=list)        # RAW_COLUMNS order (minus ingested_at placeholder)
    clean_rows: list[tuple] = field(default_factory=list)      # CLEAN_COLUMNS order
    quarantine_rows: list[tuple] = field(default_factory=list) # QUARANTINE_COLUMNS order
    cleaned: list[CleanedRow] = field(default_factory=list)
    received: int = 0
    loaded: int = 0
    duplicates: int = 0
    stale: int = 0
    quarantined: int = 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def read_encounter_file(path: Path, source_system: str, contracts: dict):
    """Read one CSV fully; yields the matched contract and parsed records.

    utf-8-sig transparently strips a BOM; newline='' lets the csv module own
    line endings (CRLF and LF) and quoted fields, including embedded newlines.
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            raise LoadError(f"LOAD_ERROR: {path.name} is empty")
        contract = match_contract(contracts, source_system, header)
        records: list[tuple[int, dict, str | None]] = []
        for row_number, record in enumerate(reader, start=1):
            if len(record) != len(header):
                raise LoadError(
                    f"LOAD_ERROR: {path.name} row {row_number} has {len(record)} fields, "
                    f"expected {len(header)}"
                )
            canonical = dict.fromkeys(CANONICAL_COLUMNS)
            extras: dict[str, str] = {}
            for col, value in zip(header, record):
                if col in contract.extra_columns:
                    extras[col] = value
                else:
                    canonical[contract.canonical_by_source[col]] = value
            records.append((
                row_number, canonical,
                json.dumps(extras, ensure_ascii=False) if extras else None,
            ))
        return contract, records


def _clean_tuple(batch_id: str, file_name: str, row_number: int, row: CleanedRow) -> tuple:
    return (
        batch_id, file_name, row_number,
        row.source_system, row.source_record_id,
        row.last_updated_ts_utc, row.last_updated_ts_raw,
        row.facility_id, row.facility_name_raw,
        row.patient_key, row.linkage_scope, row.age_band, row.sex, row.zip3,
        row.admit_date, row.admit_date_raw,
        row.discharge_date, row.discharge_date_raw,
        row.discharge_before_admit, row.length_of_stay_days,
        row.admit_year, row.admit_quarter, row.admit_month,
        row.encounter_type, row.encounter_type_raw,
        row.claim_status, row.claim_status_raw,
        row.payer_category, row.payer_name_raw,
        row.billed_amount, row.billed_amount_raw,
        row.attending_npi, row.attending_npi_raw, row.attending_provider_name_raw,
        row.primary_dx_code, row.primary_dx_raw, row.dx_outcome,
        ";".join(row.warnings) if row.warnings else None,
    )


def _process_file(
    batch_id: str, path: Path, source_system: str, sha256: str,
    cfg: RunConfig, ctx: CleanContext, state: VersionState, now: datetime,
) -> FileResult:
    contract, records = read_encounter_file(path, source_system, cfg.contracts)
    result = FileResult(path.name, source_system, sha256, contract)
    result.received = len(records)

    for row_number, canonical, extras_json in records:
        result.raw_rows.append((
            batch_id, path.name, row_number, sha256, now,
            *(canonical[c] for c in CANONICAL_COLUMNS), extras_json,
        ))
        cleaned = clean_row(canonical, ctx)
        result.cleaned.append(cleaned)

        if cleaned.is_quarantined:
            result.quarantined += 1
            result.quarantine_rows.append((
                batch_id, path.name, row_number, source_system,
                cleaned.source_record_id,
                ";".join(sorted(set(cleaned.errors))),
                json.dumps(cleaned.reasons, ensure_ascii=False),
                now,
            ))
            continue

        outcome = classify_version(
            state, cleaned.source_system, cleaned.source_record_id,
            cleaned.last_updated_ts_utc,
        ).outcome
        if outcome == "DUPLICATE":
            result.duplicates += 1
        elif outcome == "STALE":
            result.stale += 1
        else:
            result.loaded += 1
            result.clean_rows.append(_clean_tuple(batch_id, path.name, row_number, cleaned))

    return result


def _audit(con, batch_id: str, *, file_name=None, source_system=None, status: str,
           reason=None, received=None, loaded=None, duplicates=None, stale=None,
           quarantined=None, delivered_at=None, started_at=None, finished_at=None) -> None:
    duration_ms = None
    if started_at is not None and finished_at is not None:
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    con.execute(_AUDIT_SQL, [
        batch_id, file_name, source_system, status, reason,
        received, loaded, duplicates, stale, quarantined,
        delivered_at, started_at, finished_at, duration_ms,
    ])


def _register(con, batch_id: str, manifest_sha: str, status: str, delivered_at: str | None) -> None:
    con.execute(
        "INSERT INTO meta.processed_batches (batch_id, manifest_sha256, status, delivered_at, processed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [batch_id, manifest_sha, status, delivered_at, _now()],
    )


def _write_dq(con, batch_id: str, dq_counts) -> None:
    for (check_id, severity), count in sorted(dq_counts.items()):
        con.execute(
            "INSERT INTO meta.dq_results (batch_id, check_id, severity, rows_flagged) VALUES (?, ?, ?, ?)",
            [batch_id, check_id, severity, count],
        )


def _delivered_date(manifest: Manifest) -> date:
    return datetime.fromisoformat(manifest.delivered_at.replace("Z", "+00:00")).date()


def process_batch(
    con: duckdb.DuckDBPyConnection, batch_dir: Path, cfg: RunConfig, state: VersionState
) -> str:
    """Process one batch directory; returns the final status string."""
    batch_id = batch_dir.name
    started = _now()

    seen = con.execute(
        "SELECT status, manifest_sha256 FROM meta.processed_batches WHERE batch_id = ?", [batch_id]
    ).fetchone()
    manifest_path = batch_dir / "manifest.json"
    manifest_sha = sha256_of_file(manifest_path) if manifest_path.exists() else ""
    if seen:
        if seen[1] != manifest_sha:
            log.warning("batch already processed but manifest has changed; keeping first result",
                        batch_id=batch_id, previous_status=seen[0])
        else:
            log.info("batch already processed; skipping", batch_id=batch_id, previous_status=seen[0])
        return "SKIPPED"

    try:
        manifest = load_manifest(batch_dir)
    except ManifestError as exc:
        log.error("batch rejected: manifest unusable", batch_id=batch_id, reason=str(exc))
        _audit(con, batch_id, status="REJECTED", reason=str(exc),
               started_at=started, finished_at=_now())
        _register(con, batch_id, manifest_sha, "REJECTED", None)
        return "REJECTED"

    checks = validate_batch_files(batch_dir, manifest)
    failed = [c for c in checks if not c.ok]
    if failed:
        for check in checks:
            status = "REJECTED" if not check.ok else "NOT_LOADED"
            reason = "; ".join(check.reasons) if not check.ok else "BATCH_REJECTED (all-or-nothing)"
            _audit(con, batch_id, file_name=check.file_name, source_system=check.source_system,
                   status=status, reason=reason, received=check.actual_rows)
        summary = "MANIFEST_VALIDATION_FAILED: " + "; ".join(
            f"{c.file_name}: {', '.join(c.reasons)}" for c in failed
        )
        log.error("batch rejected: manifest validation failed", batch_id=batch_id,
                  failed_files=[c.file_name for c in failed])
        _audit(con, batch_id, status="REJECTED", reason=summary,
               delivered_at=manifest.delivered_at, started_at=started, finished_at=_now())
        _register(con, batch_id, manifest_sha, "REJECTED", manifest.delivered_at)
        return "REJECTED"

    # Everything below runs in memory: nothing is written until the gate passes.
    ctx = build_context(
        conventions=cfg.conventions,
        facility_aliases_cfg=cfg.facility_aliases_cfg,
        value_mappings_cfg=cfg.value_mappings_cfg,
        icd10_codes=cfg.icd10_codes,
        patient_key_secret=cfg.patient_key_secret,
        delivered_date=_delivered_date(manifest),
    )
    now = _now()
    results: list[FileResult] = []
    try:
        for mf, check in zip(manifest.files, checks):
            results.append(_process_file(
                batch_id, batch_dir / mf.file_name, mf.source_system,
                check.actual_sha256, cfg, ctx, state, now,
            ))
            fr = results[-1]
            log.info("file cleaned", batch_id=batch_id, file_name=fr.file_name,
                     schema_version=fr.contract.version, received=fr.received,
                     new_versions=fr.loaded, duplicates=fr.duplicates,
                     stale=fr.stale, quarantined=fr.quarantined)
    except (SchemaContractError, LoadError) as exc:
        offending = mf.file_name
        for m in manifest.files:
            status = "REJECTED" if m.file_name == offending else "NOT_LOADED"
            reason = str(exc) if m.file_name == offending else "BATCH_REJECTED (all-or-nothing)"
            _audit(con, batch_id, file_name=m.file_name, source_system=m.source_system,
                   status=status, reason=reason)
        log.error("batch rejected", batch_id=batch_id, file_name=offending, reason=str(exc))
        _audit(con, batch_id, status="REJECTED", reason=str(exc),
               delivered_at=manifest.delivered_at, started_at=started, finished_at=_now())
        _register(con, batch_id, manifest_sha, "REJECTED", manifest.delivered_at)
        return "REJECTED"

    received = sum(r.received for r in results)
    quarantined = sum(r.quarantined for r in results)
    dq_counts = aggregate_dq([c for r in results for c in r.cleaned])
    passed, share = gate_passes(received, quarantined, cfg.gate_threshold)

    if not passed:
        reason = (f"PUBLISH_GATE_FAILED: {quarantined}/{received} rows "
                  f"({share:.1%}) failed error-level checks; threshold {cfg.gate_threshold:.0%}")
        log.error("batch gate-failed; nothing published", batch_id=batch_id,
                  error_share=round(share, 4), threshold=cfg.gate_threshold)
        for r in results:
            _audit(con, batch_id, file_name=r.file_name, source_system=r.source_system,
                   status="GATE_FAILED", reason="BATCH_GATE_FAILED",
                   received=r.received, quarantined=r.quarantined)
        _audit(con, batch_id, status="GATE_FAILED", reason=reason,
               received=received, quarantined=quarantined,
               delivered_at=manifest.delivered_at, started_at=started, finished_at=_now())
        _write_dq(con, batch_id, dq_counts)
        _register(con, batch_id, manifest_sha, "GATE_FAILED", manifest.delivered_at)
        # The in-memory version state must forget this batch's would-be inserts.
        state_reset = VersionState.load(con)
        state.seen, state.newest = state_reset.seen, state_reset.newest
        return "GATE_FAILED"

    con.execute("BEGIN TRANSACTION")
    try:
        for r in results:
            bulk_insert(con, "raw.encounters", RAW_COLUMNS, r.raw_rows)
            bulk_insert(con, "clean.encounter_versions", CLEAN_COLUMNS, r.clean_rows)
            bulk_insert(con, "meta.quarantine", QUARANTINE_COLUMNS, r.quarantine_rows)
            _audit(con, batch_id, file_name=r.file_name, source_system=r.source_system,
                   status="ACCEPTED", received=r.received, loaded=r.loaded,
                   duplicates=r.duplicates, stale=r.stale, quarantined=r.quarantined)
        finished = _now()
        _audit(con, batch_id, status="ACCEPTED",
               received=received, loaded=sum(r.loaded for r in results),
               duplicates=sum(r.duplicates for r in results),
               stale=sum(r.stale for r in results), quarantined=quarantined,
               delivered_at=manifest.delivered_at, started_at=started, finished_at=finished)
        _write_dq(con, batch_id, dq_counts)
        _register(con, batch_id, manifest_sha, "LOADED", manifest.delivered_at)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        log.error("batch load failed; rolled back", batch_id=batch_id)
        _audit(con, batch_id, status="REJECTED", reason="LOAD_ERROR: unexpected failure, rolled back",
               delivered_at=manifest.delivered_at, started_at=started, finished_at=_now())
        _register(con, batch_id, manifest_sha, "REJECTED", manifest.delivered_at)
        raise

    log.info("batch accepted", batch_id=batch_id,
             new_versions=sum(r.loaded for r in results),
             duplicates=sum(r.duplicates for r in results),
             stale=sum(r.stale for r in results), quarantined=quarantined)
    return "ACCEPTED"
