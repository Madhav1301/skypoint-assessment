"""Batch-processing behaviour on synthetic batches: all-or-nothing rejection,
versioning counts, quarantine, publish gate, audit records, idempotent skip.
"""

from __future__ import annotations

import dataclasses

import pytest

from pipeline import db
from pipeline.ingest import CANONICAL_COLUMNS, process_batch
from pipeline.versioning import VersionState


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


@pytest.fixture
def state():
    return VersionState()


def _set(row: list[str], column: str, value: str) -> list[str]:
    row = list(row)
    row[CANONICAL_COLUMNS.index(column)] = value
    return row


def _two_file_batch(make_batch, sample_row, parent, row_count_override=None):
    return make_batch(
        parent,
        "batch_t10",
        {
            "encounters_epic_north.csv": ("EPIC_NORTH", list(CANONICAL_COLUMNS),
                                          [sample_row("EPIC_NORTH", "E1")]),
            "encounters_athena_clinics.csv": ("ATHENA_CLINICS", list(CANONICAL_COLUMNS),
                                              [sample_row("ATHENA_CLINICS", "A1"),
                                               sample_row("ATHENA_CLINICS", "A2")]),
        },
        row_count_override=row_count_override,
    )


def test_accepted_batch_loads_raw_and_clean_with_lineage(con, tmp_path, make_batch, sample_row, run_cfg, state):
    batch_dir = _two_file_batch(make_batch, sample_row, tmp_path)
    assert process_batch(con, batch_dir, run_cfg, state) == "ACCEPTED"

    assert con.execute("SELECT count(*) FROM raw.encounters").fetchone()[0] == 3
    clean = con.execute(
        "SELECT source_batch_id, source_file_name, source_row_number, source_record_id, facility_id "
        "FROM clean.encounter_versions ORDER BY source_file_name, source_row_number"
    ).fetchall()
    assert len(clean) == 3
    assert clean[0] == ("batch_t10", "encounters_athena_clinics.csv", 1, "A1", "FAC007")

    batch_row = con.execute(
        "SELECT status, rows_received, rows_loaded, rows_duplicate, rows_stale, rows_quarantined "
        "FROM meta.batch_audit WHERE file_name IS NULL"
    ).fetchone()
    assert batch_row == ("ACCEPTED", 3, 3, 0, 0, 0)


def test_duplicates_stale_and_quarantine_are_counted_not_loaded(con, tmp_path, make_batch, sample_row, run_cfg, state):
    base = sample_row("EPIC_NORTH", "E1")
    v2 = _set(base, "last_updated_ts", "2024-02-01T00:00:00Z")
    rows = [
        v2,                                                       # new version (ts v2)
        list(v2),                                                 # exact duplicate of v2
        _set(base, "last_updated_ts", "2024-01-15T10:00:00Z"),    # older than v2 -> stale
        _set(sample_row("EPIC_NORTH", "E2"), "facility_name", "Westfield Surgical Center"),  # quarantine
        # Filler rows keep the error share at 1/11 (9.1%), under the 10% gate.
        *[sample_row("EPIC_NORTH", f"E{i}") for i in range(10, 17)],
    ]
    batch_dir = make_batch(tmp_path, "batch_t11",
                           {"encounters_epic_north.csv": ("EPIC_NORTH", list(CANONICAL_COLUMNS), rows)})
    assert process_batch(con, batch_dir, run_cfg, state) == "ACCEPTED"

    audit = con.execute(
        "SELECT rows_received, rows_loaded, rows_duplicate, rows_stale, rows_quarantined "
        "FROM meta.batch_audit WHERE file_name IS NULL"
    ).fetchone()
    assert audit == (11, 8, 1, 1, 1)

    # Raw keeps everything verbatim; clean holds only the new versions.
    assert con.execute("SELECT count(*) FROM raw.encounters").fetchone()[0] == 11
    assert con.execute("SELECT count(*) FROM clean.encounter_versions").fetchone()[0] == 8

    q = con.execute(
        "SELECT source_record_id, errors FROM meta.quarantine"
    ).fetchall()
    assert q == [("E2", "UNRESOLVED_FACILITY")]


def test_stale_across_batches_never_overwrites(con, tmp_path, make_batch, sample_row, run_cfg, state):
    base = sample_row("EPIC_NORTH", "E1")
    newer = _set(_set(base, "last_updated_ts", "2024-03-01T00:00:00Z"), "claim_status", "PAID")
    older = _set(_set(base, "last_updated_ts", "2024-01-01T00:00:00Z"), "claim_status", "Submitted")

    b1 = make_batch(tmp_path, "batch_t20",
                    {"encounters_epic_north.csv": ("EPIC_NORTH", list(CANONICAL_COLUMNS), [newer])})
    b2 = make_batch(tmp_path, "batch_t21",
                    {"encounters_epic_north.csv": ("EPIC_NORTH", list(CANONICAL_COLUMNS), [older])},
                    delivered_at="2025-01-13T06:00:00Z")
    assert process_batch(con, b1, run_cfg, state) == "ACCEPTED"
    assert process_batch(con, b2, run_cfg, state) == "ACCEPTED"

    stale = con.execute(
        "SELECT rows_stale FROM meta.batch_audit WHERE batch_id='batch_t21' AND file_name IS NULL"
    ).fetchone()[0]
    assert stale == 1
    versions = con.execute(
        "SELECT claim_status FROM clean.encounter_versions WHERE source_record_id='E1'"
    ).fetchall()
    assert versions == [("PAID",)]   # the older row never entered history


def test_manifest_failure_rejects_whole_batch(con, tmp_path, make_batch, sample_row, run_cfg, state):
    batch_dir = _two_file_batch(make_batch, sample_row, tmp_path,
                                row_count_override={"encounters_epic_north.csv": 99})
    assert process_batch(con, batch_dir, run_cfg, state) == "REJECTED"
    assert con.execute("SELECT count(*) FROM raw.encounters").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM clean.encounter_versions").fetchone()[0] == 0

    audit = dict(con.execute(
        "SELECT file_name, status FROM meta.batch_audit WHERE file_name IS NOT NULL"
    ).fetchall())
    assert audit["encounters_epic_north.csv"] == "REJECTED"
    assert audit["encounters_athena_clinics.csv"] == "NOT_LOADED"
    reason = con.execute(
        "SELECT reason FROM meta.batch_audit WHERE file_name IS NULL"
    ).fetchone()[0]
    assert "MANIFEST_VALIDATION_FAILED" in reason and "ROW_COUNT_MISMATCH" in reason


def test_unknown_schema_rejects_batch_before_loading(con, tmp_path, make_batch, sample_row, run_cfg, state):
    header = list(CANONICAL_COLUMNS)
    header[header.index("billed_amount")] = "charge_total_new"  # unannounced rename
    batch_dir = make_batch(
        tmp_path, "batch_t12",
        {"encounters_epic_north.csv": ("EPIC_NORTH", header, [sample_row("EPIC_NORTH", "E1")])},
    )
    assert process_batch(con, batch_dir, run_cfg, state) == "REJECTED"
    assert con.execute("SELECT count(*) FROM raw.encounters").fetchone()[0] == 0
    reason = con.execute("SELECT reason FROM meta.batch_audit WHERE file_name IS NULL").fetchone()[0]
    assert "SCHEMA_CONTRACT" in reason and "charge_total_new" in reason


def test_publish_gate_blocks_bad_batch_entirely(con, tmp_path, make_batch, sample_row, run_cfg, state):
    rows = [
        _set(sample_row("EPIC_NORTH", "E1"), "facility_name", "Westfield Surgical Center"),
        _set(sample_row("EPIC_NORTH", "E2"), "facility_name", "Westfield Surgical Center"),
        sample_row("EPIC_NORTH", "E3"),
    ]  # 2/3 error rows = 66% > 10% threshold
    batch_dir = make_batch(tmp_path, "batch_t13",
                           {"encounters_epic_north.csv": ("EPIC_NORTH", list(CANONICAL_COLUMNS), rows)})
    assert process_batch(con, batch_dir, run_cfg, state) == "GATE_FAILED"

    # Nothing published anywhere — not even raw or quarantine.
    for table in ("raw.encounters", "clean.encounter_versions", "meta.quarantine"):
        assert con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table

    status, reason = con.execute(
        "SELECT status, reason FROM meta.batch_audit WHERE file_name IS NULL"
    ).fetchone()
    assert status == "GATE_FAILED"
    assert "PUBLISH_GATE_FAILED" in reason and "66.7%" in reason

    dq = dict(con.execute(
        "SELECT check_id, rows_flagged FROM meta.dq_results WHERE severity='error'"
    ).fetchall())
    assert dq.get("UNRESOLVED_FACILITY") == 2

    # The in-memory version state forgot the gate-failed rows: the same good
    # row arriving in a later batch is NEW, not a duplicate.
    retry = make_batch(tmp_path, "batch_t14",
                       {"encounters_epic_north.csv": ("EPIC_NORTH", list(CANONICAL_COLUMNS),
                                                      [sample_row("EPIC_NORTH", "E3")])},
                       delivered_at="2025-01-13T06:00:00Z")
    assert process_batch(con, retry, run_cfg, state) == "ACCEPTED"
    assert con.execute("SELECT count(*) FROM clean.encounter_versions").fetchone()[0] == 1


def test_reprocessing_same_batch_is_noop(con, tmp_path, make_batch, sample_row, run_cfg, state):
    batch_dir = _two_file_batch(make_batch, sample_row, tmp_path)
    assert process_batch(con, batch_dir, run_cfg, state) == "ACCEPTED"
    raw_before = con.execute("SELECT count(*) FROM raw.encounters").fetchone()[0]
    audit_before = con.execute("SELECT count(*) FROM meta.batch_audit").fetchone()[0]

    assert process_batch(con, batch_dir, run_cfg, VersionState.load(con)) == "SKIPPED"
    assert con.execute("SELECT count(*) FROM raw.encounters").fetchone()[0] == raw_before
    assert con.execute("SELECT count(*) FROM meta.batch_audit").fetchone()[0] == audit_before
