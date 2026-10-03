"""Unit tests for manifest loading and pre-load validation."""

from __future__ import annotations

import pytest

from pipeline.ingest import CANONICAL_COLUMNS
from pipeline.manifest import ManifestError, load_manifest, validate_batch_files


@pytest.fixture
def one_file_batch(tmp_path, make_batch, sample_row):
    def build(**kwargs):
        return make_batch(
            tmp_path,
            "batch_t01",
            {"encounters_epic_north.csv": ("EPIC_NORTH", list(CANONICAL_COLUMNS),
                                           [sample_row("EPIC_NORTH", "E1"),
                                            sample_row("EPIC_NORTH", "E2")])},
            **kwargs,
        )
    return build


def test_valid_batch_passes(one_file_batch):
    batch_dir = one_file_batch()
    manifest = load_manifest(batch_dir)
    checks = validate_batch_files(batch_dir, manifest)
    assert all(c.ok for c in checks)
    assert checks[0].actual_rows == 2


def test_row_count_mismatch_detected(one_file_batch):
    batch_dir = one_file_batch(row_count_override={"encounters_epic_north.csv": 5})
    manifest = load_manifest(batch_dir)
    checks = validate_batch_files(batch_dir, manifest)
    assert not checks[0].ok
    assert any("ROW_COUNT_MISMATCH" in r for r in checks[0].reasons)
    assert "expected=5" in checks[0].reasons[0] and "actual=2" in checks[0].reasons[0]


def test_tampered_file_fails_sha(one_file_batch):
    batch_dir = one_file_batch()
    csv_path = batch_dir / "encounters_epic_north.csv"
    # Append whitespace after the manifest was computed: same row count, new bytes.
    with open(csv_path, "a", encoding="utf-8", newline="") as f:
        f.write(" ")
    manifest = load_manifest(batch_dir)
    checks = validate_batch_files(batch_dir, manifest)
    assert not checks[0].ok
    assert "SHA256_MISMATCH" in checks[0].reasons


def test_missing_file_detected(one_file_batch):
    batch_dir = one_file_batch()
    (batch_dir / "encounters_epic_north.csv").unlink()
    manifest = load_manifest(batch_dir)
    checks = validate_batch_files(batch_dir, manifest)
    assert not checks[0].ok
    assert checks[0].reasons == ("FILE_MISSING",)


def test_missing_manifest_raises(tmp_path):
    batch_dir = tmp_path / "batch_t02"
    batch_dir.mkdir()
    with pytest.raises(ManifestError, match="MANIFEST_MISSING"):
        load_manifest(batch_dir)


def test_quoted_fields_do_not_miscount(tmp_path, make_batch, sample_row):
    row = sample_row("EPIC_NORTH", "E1")
    row[CANONICAL_COLUMNS.index("chief_complaint")] = 'line one\nline "two", quoted'
    batch_dir = make_batch(
        tmp_path, "batch_t03",
        {"encounters_epic_north.csv": ("EPIC_NORTH", list(CANONICAL_COLUMNS), [row])},
    )
    manifest = load_manifest(batch_dir)
    checks = validate_batch_files(batch_dir, manifest)
    assert checks[0].ok
    assert checks[0].actual_rows == 1
