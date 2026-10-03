"""Shared fixtures: synthetic batch builder and real config loading."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

# Inside the container the pack is mounted read-only at $DATA_DIR (/data);
# locally it lives in the repo. Every test resolves data through this path.
DATA_DIR = Path(os.environ.get("DATA_DIR") or REPO_ROOT / "data" / "candidate_pack")

from pipeline.config import load_yaml_config  # noqa: E402
from pipeline.ingest import CANONICAL_COLUMNS  # noqa: E402


@pytest.fixture(scope="session")
def contracts() -> dict:
    return load_yaml_config(REPO_ROOT / "config", "schema_contracts.yaml")["contracts"]


@pytest.fixture(scope="session")
def value_mappings() -> dict:
    return load_yaml_config(REPO_ROOT / "config", "value_mappings.yaml")


@pytest.fixture(scope="session")
def facility_aliases() -> dict:
    return load_yaml_config(REPO_ROOT / "config", "facility_aliases.yaml")


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA_DIR


@pytest.fixture(scope="session")
def icd10_codes() -> set[str]:
    from pipeline.config import load_icd10_reference

    return set(load_icd10_reference(DATA_DIR).keys())


@pytest.fixture(scope="session")
def run_cfg(contracts, value_mappings, facility_aliases, icd10_codes):
    from pipeline.config import load_source_conventions
    from pipeline.ingest import RunConfig

    conventions = load_source_conventions(DATA_DIR)["source_systems"]
    return RunConfig(
        contracts=contracts,
        conventions=conventions,
        facility_aliases_cfg=facility_aliases,
        value_mappings_cfg=value_mappings,
        icd10_codes=icd10_codes,
        patient_key_secret="test-secret",
        gate_threshold=0.10,
    )


_FACILITY_FOR_SYSTEM = {
    "EPIC_NORTH": "Lakeshore General Hospital",
    "ATHENA_CLINICS": "Eastgate Urgent Care",
    "LEGACY_MEDITECH": "Riverbend Community Hospital",
}


def _sample_row(source_system: str, record_id: str) -> list[str]:
    """One synthetic encounter row in canonical column order (fake values only)."""
    values = {
        "source_system": source_system,
        "source_record_id": record_id,
        "facility_name": _FACILITY_FOR_SYSTEM.get(source_system, "Lakeshore General Hospital"),
        "patient_mrn": "TEST-MRN-1",
        "patient_first_name": "Test",
        "patient_last_name": "Patient",
        "patient_dob": "1980-01-01",
        "patient_sex": "F",
        "patient_zip": "53000",
        "patient_phone": "(555) 555-0100",
        "admit_date": "2024-01-10",
        "discharge_date": "2024-01-12",
        "encounter_type": "Inpatient",
        "attending_npi": "1440029787",
        "attending_provider_name": "Dr. Test Provider",
        "primary_dx_code": "I10",
        "chief_complaint": "test complaint",
        "payer_name": "Medicare",
        "billed_amount": "1234.56",
        "claim_status": "PAID",
        "last_updated_ts": "2024-01-15T10:00:00Z",
    }
    return [values[c] for c in CANONICAL_COLUMNS]


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_batch(
    parent: Path,
    batch_id: str,
    files: dict[str, tuple[str, list[str], list[list[str]]]],
    delivered_at: str = "2025-01-06T06:00:00Z",
    row_count_override: dict[str, int] | None = None,
) -> Path:
    """Create a batch directory with CSVs and a manifest derived from them.

    files: file_name -> (source_system, header, rows)
    row_count_override: file_name -> wrong count, to simulate a bad manifest.
    """
    batch_dir = parent / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for file_name, (source_system, header, rows) in files.items():
        path = batch_dir / file_name
        _write_csv(path, header, rows)
        count = len(rows)
        if row_count_override and file_name in row_count_override:
            count = row_count_override[file_name]
        entries.append({
            "file_name": file_name,
            "source_system": source_system,
            "row_count": count,
            "sha256": _sha256_of(path),
        })
    manifest = {"batch_id": batch_id, "delivered_at": delivered_at, "files": entries}
    (batch_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return batch_dir


@pytest.fixture
def make_batch():
    return _make_batch


@pytest.fixture
def sample_row():
    return _sample_row
