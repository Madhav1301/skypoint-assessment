"""End-to-end integration tests on the real data pack.

Covers the brief's required proofs:
  - idempotency: batch-by-batch == full rebuild, and re-runs are no-ops
  - the publish gate is exercised separately in unit tests; here we prove
    batches 001-003 pass it and batch_004 is rejected at the manifest
  - as-of reporting: state reconstructed "as known at end of batch_002"
    equals an actual run stopped after batch_002
  - current-state correctness under stale replays (encounter 139507)
  - SCD2 point-in-time joins
  - the Task 7 export contract (columns, sort, filters, readmit flag)
"""

from __future__ import annotations

import csv
import filecmp
import shutil
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import db
from pipeline.ingest import process_batch
from pipeline.model import build_gold, build_reference, write_outputs
from pipeline.versioning import VersionState

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "candidate_pack"

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "landing").exists(), reason=f"data pack not found at {DATA_DIR}"
)

EXPORT_HEADER = [
    "encounter_key", "source_system", "source_record_id", "facility_id",
    "facility_name", "facility_type", "patient_key", "age_band", "sex",
    "patient_zip3", "admit_date", "discharge_date", "length_of_stay_days",
    "encounter_type", "primary_dx_code", "dx_description", "chronic_category",
    "attending_npi", "attending_specialty_at_encounter",
    "attending_employment_status_at_encounter", "payer_category",
    "billed_amount_usd", "claim_status", "readmit_30d_flag", "version_count",
    "source_batch_id", "source_file_name", "source_row_number",
]

# Outputs compared byte-for-byte between runs. batch_audit carries wall-clock
# timings and is deliberately excluded.
DETERMINISTIC_OUTPUTS = [
    "fact_encounter_versions.csv", "fact_encounter_current.csv",
    "dim_provider.csv", "dim_patient.csv", "dim_facility.csv",
    "dim_diagnosis.csv", "dim_payer.csv", "dim_date.csv",
    "quarantine.csv", "dq_results.csv", "chronic_acute_encounters.csv",
]


def run_pipeline(landing_source: Path, workdir: Path, data_dir: Path = DATA_DIR):
    """Run ingest + model + outputs against a landing dir; returns handles."""
    con = db.connect(workdir / "wh.duckdb")
    state = VersionState.load(con)
    cfg = _run_cfg_cache["cfg"]
    results = {}
    for batch_dir in sorted(p for p in landing_source.iterdir() if p.is_dir()):
        results[batch_dir.name] = process_batch(con, batch_dir, cfg, state)
    build_reference(con, data_dir)
    build_gold(con)
    out = workdir / "output"
    write_outputs(con, out)
    return SimpleNamespace(con=con, out=out, results=results)


_run_cfg_cache: dict = {}


@pytest.fixture(scope="module", autouse=True)
def _cache_cfg(run_cfg):
    _run_cfg_cache["cfg"] = run_cfg


@pytest.fixture(scope="module")
def pipe(tmp_path_factory, run_cfg):
    workdir = tmp_path_factory.mktemp("full")
    return run_pipeline(DATA_DIR / "landing", workdir)


def _subset_landing(tmp_path: Path, batches: list[str]) -> Path:
    landing = tmp_path / "landing"
    landing.mkdir(parents=True)
    for b in batches:
        shutil.copytree(DATA_DIR / "landing" / b, landing / b)
    return landing


# ---------------------------------------------------------------- outcomes

def test_batch_statuses(pipe):
    assert pipe.results == {
        "batch_001": "ACCEPTED",
        "batch_002": "ACCEPTED",
        "batch_003": "ACCEPTED",
        "batch_004": "REJECTED",
    }


def test_reconciliation_identity_holds_per_file(pipe):
    rows = pipe.con.execute(
        "SELECT batch_id, file_name, rows_received, rows_loaded, rows_duplicate, "
        "rows_stale, rows_quarantined FROM meta.batch_audit "
        "WHERE status = 'ACCEPTED' AND file_name IS NOT NULL"
    ).fetchall()
    assert len(rows) == 9  # 3 files x 3 accepted batches
    for batch_id, file_name, received, loaded, dup, stale, quarantined in rows:
        assert received == loaded + dup + stale + quarantined, (batch_id, file_name)


def test_totals_reconcile_from_landing_to_layers(pipe):
    con = pipe.con
    raw = con.execute("SELECT count(*) FROM raw.encounters").fetchone()[0]
    versions = con.execute("SELECT count(*) FROM clean.encounter_versions").fetchone()[0]
    quarantined = con.execute("SELECT count(*) FROM meta.quarantine").fetchone()[0]
    dup, stale = con.execute(
        "SELECT sum(rows_duplicate), sum(rows_stale) FROM meta.batch_audit "
        "WHERE status='ACCEPTED' AND file_name IS NULL"
    ).fetchone()

    assert raw == 3636                      # batches 001-003, nothing from 004
    assert raw == versions + dup + stale + quarantined
    # The planted facility traps: TEST FACILITY (7) + Westfield (25).
    assert quarantined == 32
    reasons = dict(pipe.con.execute(
        "SELECT errors, count(*) FROM meta.quarantine GROUP BY errors"
    ).fetchall())
    assert reasons == {"UNRESOLVED_FACILITY": 32}


def test_batches_001_to_003_pass_the_gate(pipe):
    shares = pipe.con.execute(
        "SELECT batch_id, rows_quarantined * 1.0 / rows_received FROM meta.batch_audit "
        "WHERE status='ACCEPTED' AND file_name IS NULL ORDER BY batch_id"
    ).fetchall()
    assert len(shares) == 3
    for batch_id, share in shares:
        assert share <= 0.10, batch_id


def test_stale_replay_cannot_flip_current_state(pipe):
    # ATHENA 139507: batch_003 replays the batch_001 version (same instant,
    # new schema spelling). Current state must remain the batch_002 version.
    row = pipe.con.execute(
        "SELECT claim_status, billed_amount, source_batch_id, version_count "
        "FROM gold.fact_encounter_current "
        "WHERE source_system='ATHENA_CLINICS' AND source_record_id='139507'"
    ).fetchone()
    status, amount, batch, version_count = row
    assert status == "PAID"
    assert str(amount) == "71.70"
    assert batch == "batch_002"
    assert version_count == 2


def test_history_and_current_counts(pipe):
    encounters = pipe.con.execute(
        "SELECT count(DISTINCT (source_system, source_record_id)) FROM clean.encounter_versions"
    ).fetchone()[0]
    current = pipe.con.execute("SELECT count(*) FROM gold.fact_encounter_current").fetchone()[0]
    assert current == encounters
    multi = pipe.con.execute(
        "SELECT count(*) FROM gold.fact_encounter_current WHERE version_count > 1"
    ).fetchone()[0]
    assert multi > 200  # the feed genuinely re-versions encounters


def test_scd2_point_in_time_join(pipe):
    specialty, status = pipe.con.execute("""
        SELECT specialty, employment_status FROM gold.dim_provider
        WHERE npi='1228088641' AND DATE '2024-03-15' >= valid_from AND DATE '2024-03-15' < valid_to
    """).fetchone()
    assert (specialty, status) == ("Emergency Medicine", "Affiliated")
    status_aug = pipe.con.execute("""
        SELECT employment_status FROM gold.dim_provider
        WHERE npi='1228088641' AND DATE '2024-08-01' >= valid_from AND DATE '2024-08-01' < valid_to
    """).fetchone()[0]
    assert status_aug == "Employed"

    names = pipe.con.execute("""
        SELECT provider_last_name FROM gold.dim_provider WHERE npi='1472671472' ORDER BY valid_from
    """).fetchall()
    assert [n[0] for n in names] == ["King", "Olson"]


# ---------------------------------------------------------------- export

def test_export_contract(pipe):
    path = pipe.out / "chronic_acute_encounters.csv"
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == EXPORT_HEADER
        rows = list(reader)

    assert len(rows) > 50  # meaningful export

    chronic_codes = {
        r[0] for r in pipe.con.execute(
            "SELECT icd10_code FROM gold.dim_diagnosis WHERE is_chronic"
        ).fetchall()
    }
    prev_sort_key = None
    for r in rows:
        assert r["encounter_type"] in ("INPATIENT", "OBSERVATION", "EMERGENCY")
        assert r["claim_status"] in ("PAID", "DENIED", "SUBMITTED")  # never VOID or blank
        assert r["admit_date"].startswith("2024-")
        assert float(r["billed_amount_usd"]) >= 5000.00
        assert "." in r["billed_amount_usd"] and len(r["billed_amount_usd"].split(".")[1]) == 2
        assert r["primary_dx_code"] in chronic_codes
        assert r["facility_id"].startswith("FAC")
        if r["encounter_type"] == "INPATIENT":
            assert r["readmit_30d_flag"] in ("0", "1")
        else:
            assert r["readmit_30d_flag"] == ""
        key = (r["admit_date"], r["source_system"], r["source_record_id"])
        assert prev_sort_key is None or key >= prev_sort_key
        prev_sort_key = key

    # Lineage: every export row's (batch, file, row) must exist in raw with
    # the same source_record_id.
    sample = rows[0]
    match = pipe.con.execute(
        "SELECT source_record_id FROM raw.encounters "
        "WHERE batch_id=? AND file_name=? AND source_row_number=?",
        [sample["source_batch_id"], sample["source_file_name"], int(sample["source_row_number"])],
    ).fetchone()
    assert match[0] == sample["source_record_id"]


def test_export_contains_no_phi_shapes(pipe):
    import re

    text = (pipe.out / "chronic_acute_encounters.csv").read_text(encoding="utf-8")
    assert not re.search(r"\(\d{3}\) \d{3}-\d{4}", text)        # phone shapes
    # Full ZIPs never appear: the zip3 column is 3 digits by construction.
    with open(pipe.out / "chronic_acute_encounters.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            assert len(row["patient_zip3"]) in (0, 3)


# ------------------------------------------------- idempotency and as-of

def test_incremental_equals_full_rebuild(tmp_path, pipe):
    # A: land batch_001 alone, run; then add 002+003 and run again.
    landing_a = _subset_landing(tmp_path / "a", ["batch_001"])
    work_a = tmp_path / "work_a"
    first = run_pipeline(landing_a, work_a)
    assert first.results == {"batch_001": "ACCEPTED"}
    first.con.close()
    for b in ("batch_002", "batch_003"):
        shutil.copytree(DATA_DIR / "landing" / b, landing_a / b)
    second = run_pipeline(landing_a, work_a)   # same warehouse: 001 skips
    assert second.results["batch_001"] == "SKIPPED"
    second.con.close()

    # B: fresh full rebuild of the same three batches.
    landing_b = _subset_landing(tmp_path / "b", ["batch_001", "batch_002", "batch_003"])
    full = run_pipeline(landing_b, tmp_path / "work_b")
    full.con.close()

    for name in DETERMINISTIC_OUTPUTS:
        assert filecmp.cmp(work_a / "output" / name, tmp_path / "work_b" / "output" / name,
                           shallow=False), f"{name} differs between incremental and rebuild"


def test_rerun_of_everything_is_a_noop(tmp_path, pipe):
    results = {}
    state = VersionState.load(pipe.con)
    for batch_dir in sorted((DATA_DIR / "landing").iterdir()):
        if batch_dir.is_dir():
            results[batch_dir.name] = process_batch(pipe.con, batch_dir, _run_cfg_cache["cfg"], state)
    assert set(results.values()) == {"SKIPPED"}

    out2 = tmp_path / "out2"
    write_outputs(pipe.con, out2)
    for name in DETERMINISTIC_OUTPUTS + ["dq_report_batch_001.md", "dq_report_batch_004.md"]:
        assert filecmp.cmp(pipe.out / name, out2 / name, shallow=False), name


def test_as_of_batch_002_reporting(tmp_path, pipe):
    """Query pattern 5: totals as known at end of batch_002 must equal an
    actual run stopped after batch_002."""
    as_of_sql = """
        WITH as_of AS (
            SELECT * FROM clean.encounter_versions
            WHERE source_batch_id <= 'batch_002'
            QUALIFY row_number() OVER (
                PARTITION BY source_system, source_record_id
                ORDER BY last_updated_ts_utc DESC) = 1
        )
        SELECT facility_id, admit_year, admit_month, encounter_type,
               count(*) AS encounters, sum(billed_amount) AS billed
        FROM as_of
        WHERE claim_status IS NOT NULL AND claim_status <> 'VOID'
        GROUP BY ALL ORDER BY ALL
    """
    reconstructed = pipe.con.execute(as_of_sql).fetchall()

    landing = _subset_landing(tmp_path, ["batch_001", "batch_002"])
    partial = run_pipeline(landing, tmp_path / "work")
    actual = partial.con.execute("""
        SELECT facility_id, admit_year, admit_month, encounter_type,
               count(*) AS encounters, sum(billed_amount) AS billed
        FROM gold.fact_encounter_current
        WHERE claim_status IS NOT NULL AND claim_status <> 'VOID'
        GROUP BY ALL ORDER BY ALL
    """).fetchall()
    partial.con.close()

    assert reconstructed == actual
