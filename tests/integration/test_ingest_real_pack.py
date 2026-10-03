"""Integration tests against the real data pack (Task 1 acceptance).

Verified expectations come from profiling the pack:
  - batches 001-003 are valid: 2863 + 585 + 188 = 3636 rows
  - batch_004's EPIC file is truncated (18 rows vs 22 declared, SHA mismatch)
  - ATHENA_CLINICS switches to schema v2 (renames + reorder + BOM) in batch_003
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pipeline import db
from pipeline.config import load_yaml_config
from pipeline.ingest import process_batch

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("DATA_DIR", str(REPO_ROOT / "data" / "candidate_pack")))

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "landing").exists(),
    reason=f"data pack not found at {DATA_DIR}",
)


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "wh.duckdb")
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def contracts_cfg():
    return load_yaml_config(REPO_ROOT / "config", "schema_contracts.yaml")["contracts"]


def run_all_batches(con, contracts_cfg) -> dict[str, str]:
    results = {}
    for batch_dir in sorted((DATA_DIR / "landing").iterdir()):
        if batch_dir.is_dir():
            results[batch_dir.name] = process_batch(con, batch_dir, contracts_cfg)
    return results


def test_full_ingest_of_the_data_pack(con, contracts_cfg):
    results = run_all_batches(con, contracts_cfg)
    assert results == {
        "batch_001": "ACCEPTED",
        "batch_002": "ACCEPTED",
        "batch_003": "ACCEPTED",
        "batch_004": "REJECTED",
    }

    assert con.execute("SELECT count(*) FROM raw.encounters").fetchone()[0] == 3636
    assert con.execute(
        "SELECT count(*) FROM raw.encounters WHERE batch_id = 'batch_004'"
    ).fetchone()[0] == 0

    per_batch = dict(con.execute(
        "SELECT batch_id, count(*) FROM raw.encounters GROUP BY batch_id"
    ).fetchall())
    assert per_batch == {"batch_001": 2863, "batch_002": 585, "batch_003": 188}

    reason = con.execute(
        "SELECT reason FROM meta.batch_audit WHERE batch_id = 'batch_004' AND file_name IS NULL"
    ).fetchone()[0]
    assert "encounters_epic_north.csv" in reason
    assert "ROW_COUNT_MISMATCH" in reason and "SHA256_MISMATCH" in reason


def test_athena_schema_v2_lands_in_canonical_columns(con, contracts_cfg):
    run_all_batches(con, contracts_cfg)
    count, with_extras, with_amount, with_npi = con.execute("""
        SELECT count(*),
               count(extras_json),
               count(billed_amount),
               count(attending_npi)
        FROM raw.encounters
        WHERE batch_id = 'batch_003' AND file_name = 'encounters_athena_clinics.csv'
    """).fetchone()
    assert count == 74
    assert with_extras == 74          # encounter_source preserved for every v2 row
    assert with_amount == 74          # total_charge mapped into billed_amount
    assert with_npi == 74             # attending_provider_npi mapped into attending_npi


def test_rerun_is_a_noop(con, contracts_cfg):
    first = run_all_batches(con, contracts_cfg)
    raw_count = con.execute("SELECT count(*) FROM raw.encounters").fetchone()[0]
    audit_count = con.execute("SELECT count(*) FROM meta.batch_audit").fetchone()[0]

    second = run_all_batches(con, contracts_cfg)
    assert set(second.values()) == {"SKIPPED"}
    assert con.execute("SELECT count(*) FROM raw.encounters").fetchone()[0] == raw_count
    assert con.execute("SELECT count(*) FROM meta.batch_audit").fetchone()[0] == audit_count
    assert first["batch_004"] == "REJECTED"
