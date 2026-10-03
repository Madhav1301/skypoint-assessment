"""Batch ingestion (Task 1): manifest gate, schema contracts, raw layer, audit.

A batch is all-or-nothing: every file is validated and fully read before a
single row is inserted, and the inserts for the whole batch share one
transaction. A failed batch leaves only audit rows behind.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from . import logging_setup as log
from .manifest import Manifest, ManifestError, load_manifest, sha256_of_file, validate_batch_files
from .schema_contract import ContractMatch, SchemaContractError, match_contract

CANONICAL_COLUMNS = [
    "source_system", "source_record_id", "facility_name", "patient_mrn",
    "patient_first_name", "patient_last_name", "patient_dob", "patient_sex",
    "patient_zip", "patient_phone", "admit_date", "discharge_date",
    "encounter_type", "attending_npi", "attending_provider_name",
    "primary_dx_code", "chief_complaint", "payer_name", "billed_amount",
    "claim_status", "last_updated_ts",
]

_INSERT_SQL = (
    "INSERT INTO raw.encounters (batch_id, file_name, source_row_number, file_sha256, ingested_at, "
    + ", ".join(CANONICAL_COLUMNS)
    + ", extras_json) VALUES (" + ", ".join(["?"] * (5 + len(CANONICAL_COLUMNS) + 1)) + ")"
)

_AUDIT_SQL = """
    INSERT INTO meta.batch_audit (batch_id, file_name, source_system, status, reason,
        rows_received, rows_loaded, rows_duplicate, rows_stale, rows_quarantined,
        delivered_at, started_at, finished_at, duration_ms)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class LoadError(Exception):
    pass


@dataclass
class FileLoad:
    file_name: str
    source_system: str
    sha256: str
    contract: ContractMatch
    rows: list[tuple]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def read_encounter_file(path: Path, source_system: str, contracts: dict) -> tuple[ContractMatch, list[tuple]]:
    """Read one CSV fully; returns the matched contract and canonical row tuples.

    utf-8-sig transparently strips a BOM; newline='' lets the csv module own
    line endings (CRLF and LF) and quoted fields, including embedded newlines.
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            raise LoadError(f"LOAD_ERROR: {path.name} is empty")
        contract = match_contract(contracts, source_system, header)
        rows: list[tuple] = []
        for row_number, record in enumerate(reader, start=1):
            if len(record) != len(header):
                raise LoadError(
                    f"LOAD_ERROR: {path.name} row {row_number} has {len(record)} fields, expected {len(header)}"
                )
            canonical = dict.fromkeys(CANONICAL_COLUMNS)
            extras: dict[str, str] = {}
            for col, value in zip(header, record):
                if col in contract.extra_columns:
                    extras[col] = value
                else:
                    canonical[contract.canonical_by_source[col]] = value
            rows.append((
                row_number,
                tuple(canonical[c] for c in CANONICAL_COLUMNS),
                json.dumps(extras, ensure_ascii=False) if extras else None,
            ))
        return contract, rows


def _audit(con: duckdb.DuckDBPyConnection, batch_id: str, *, file_name: str | None = None,
           source_system: str | None = None, status: str, reason: str | None = None,
           rows_received: int | None = None, rows_loaded: int | None = None,
           delivered_at: str | None = None, started_at: datetime | None = None,
           finished_at: datetime | None = None) -> None:
    duration_ms = None
    if started_at is not None and finished_at is not None:
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    con.execute(_AUDIT_SQL, [
        batch_id, file_name, source_system, status, reason,
        rows_received, rows_loaded, None, None, None,
        delivered_at, started_at, finished_at, duration_ms,
    ])


def _register(con: duckdb.DuckDBPyConnection, batch_id: str, manifest_sha: str,
              status: str, delivered_at: str | None) -> None:
    con.execute(
        "INSERT INTO meta.processed_batches (batch_id, manifest_sha256, status, delivered_at, processed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [batch_id, manifest_sha, status, delivered_at, _now()],
    )


def process_batch(con: duckdb.DuckDBPyConnection, batch_dir: Path, contracts: dict) -> str:
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
            if check.ok:
                _audit(con, batch_id, file_name=check.file_name, source_system=check.source_system,
                       status="NOT_LOADED", reason="BATCH_REJECTED (all-or-nothing)",
                       rows_received=check.actual_rows)
            else:
                _audit(con, batch_id, file_name=check.file_name, source_system=check.source_system,
                       status="REJECTED", reason="; ".join(check.reasons),
                       rows_received=check.actual_rows)
        summary = "MANIFEST_VALIDATION_FAILED: " + "; ".join(
            f"{c.file_name}: {', '.join(c.reasons)}" for c in failed
        )
        log.error("batch rejected: manifest validation failed", batch_id=batch_id,
                  failed_files=[c.file_name for c in failed])
        _audit(con, batch_id, status="REJECTED", reason=summary,
               delivered_at=manifest.delivered_at, started_at=started, finished_at=_now())
        _register(con, batch_id, manifest_sha, "REJECTED", manifest.delivered_at)
        return "REJECTED"

    # Validation passed: read every file completely before inserting anything.
    loads: list[FileLoad] = []
    try:
        for mf, check in zip(manifest.files, checks):
            contract, rows = read_encounter_file(batch_dir / mf.file_name, mf.source_system, contracts)
            loads.append(FileLoad(mf.file_name, mf.source_system, check.actual_sha256, contract, rows))
            log.info("file read", batch_id=batch_id, file_name=mf.file_name,
                     schema_version=contract.version, rows=len(rows))
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

    ingested_at = _now()
    con.execute("BEGIN TRANSACTION")
    try:
        for fl in loads:
            params = [
                (batch_id, fl.file_name, row_number, fl.sha256, ingested_at, *values, extras_json)
                for row_number, values, extras_json in fl.rows
            ]
            if params:
                con.executemany(_INSERT_SQL, params)
            _audit(con, batch_id, file_name=fl.file_name, source_system=fl.source_system,
                   status="ACCEPTED", rows_received=len(fl.rows), rows_loaded=len(fl.rows))
        finished = _now()
        _audit(con, batch_id, status="ACCEPTED",
               rows_received=sum(len(fl.rows) for fl in loads),
               rows_loaded=sum(len(fl.rows) for fl in loads),
               delivered_at=manifest.delivered_at, started_at=started, finished_at=finished)
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
             rows_loaded=sum(len(fl.rows) for fl in loads))
    return "ACCEPTED"
