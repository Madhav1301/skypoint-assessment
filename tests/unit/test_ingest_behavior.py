"""Unit tests for batch processing behaviour on synthetic batches:
all-or-nothing rejection, audit records, skip-on-reprocess.
"""

from __future__ import annotations

import pytest

from pipeline import db
from pipeline.ingest import CANONICAL_COLUMNS, process_batch


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


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


def test_accepted_batch_loads_raw_with_lineage(con, tmp_path, make_batch, sample_row, contracts):
    batch_dir = _two_file_batch(make_batch, sample_row, tmp_path)
    status = process_batch(con, batch_dir, contracts)
    assert status == "ACCEPTED"

    rows = con.execute(
        "SELECT batch_id, file_name, source_row_number, source_record_id FROM raw.encounters ORDER BY file_name, source_row_number"
    ).fetchall()
    assert len(rows) == 3
    assert rows[0] == ("batch_t10", "encounters_athena_clinics.csv", 1, "A1")
    sha = con.execute("SELECT DISTINCT file_sha256 FROM raw.encounters WHERE file_name = 'encounters_epic_north.csv'").fetchone()[0]
    assert len(sha) == 64

    batch_row = con.execute(
        "SELECT status, rows_received, rows_loaded FROM meta.batch_audit WHERE file_name IS NULL"
    ).fetchone()
    assert batch_row == ("ACCEPTED", 3, 3)


def test_manifest_failure_rejects_whole_batch(con, tmp_path, make_batch, sample_row, contracts):
    batch_dir = _two_file_batch(
        make_batch, sample_row, tmp_path,
        row_count_override={"encounters_epic_north.csv": 99},
    )
    status = process_batch(con, batch_dir, contracts)
    assert status == "REJECTED"
    # All-or-nothing: the valid athena file must not load either.
    assert con.execute("SELECT count(*) FROM raw.encounters").fetchone()[0] == 0

    audit = dict(con.execute(
        "SELECT file_name, status FROM meta.batch_audit WHERE file_name IS NOT NULL"
    ).fetchall())
    assert audit["encounters_epic_north.csv"] == "REJECTED"
    assert audit["encounters_athena_clinics.csv"] == "NOT_LOADED"

    batch_reason = con.execute(
        "SELECT reason FROM meta.batch_audit WHERE file_name IS NULL"
    ).fetchone()[0]
    assert "MANIFEST_VALIDATION_FAILED" in batch_reason
    assert "ROW_COUNT_MISMATCH" in batch_reason


def test_unknown_schema_rejects_batch_before_loading(con, tmp_path, make_batch, sample_row, contracts):
    header = list(CANONICAL_COLUMNS)
    header[header.index("billed_amount")] = "charge_total_new"  # unannounced rename
    row = sample_row("EPIC_NORTH", "E1")
    batch_dir = make_batch(
        tmp_path, "batch_t11",
        {"encounters_epic_north.csv": ("EPIC_NORTH", header, [row])},
    )
    status = process_batch(con, batch_dir, contracts)
    assert status == "REJECTED"
    assert con.execute("SELECT count(*) FROM raw.encounters").fetchone()[0] == 0
    reason = con.execute(
        "SELECT reason FROM meta.batch_audit WHERE file_name IS NULL"
    ).fetchone()[0]
    assert "SCHEMA_CONTRACT" in reason and "charge_total_new" in reason


def test_reprocessing_same_batch_is_noop(con, tmp_path, make_batch, sample_row, contracts):
    batch_dir = _two_file_batch(make_batch, sample_row, tmp_path)
    assert process_batch(con, batch_dir, contracts) == "ACCEPTED"
    raw_before = con.execute("SELECT count(*) FROM raw.encounters").fetchone()[0]
    audit_before = con.execute("SELECT count(*) FROM meta.batch_audit").fetchone()[0]

    assert process_batch(con, batch_dir, contracts) == "SKIPPED"
    assert con.execute("SELECT count(*) FROM raw.encounters").fetchone()[0] == raw_before
    assert con.execute("SELECT count(*) FROM meta.batch_audit").fetchone()[0] == audit_before
