"""Manifest loading and pre-load validation (Task 1, gate 1).

Every file in a batch is verified against the manifest's row count and
SHA-256 before anything is loaded. Row counts are CSV records excluding the
header, read with the csv module so quoted fields cannot miscount.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path


class ManifestError(Exception):
    """The manifest itself is missing or malformed."""


@dataclass(frozen=True)
class ManifestFile:
    file_name: str
    source_system: str
    row_count: int
    sha256: str


@dataclass(frozen=True)
class Manifest:
    batch_id: str
    delivered_at: str
    files: tuple[ManifestFile, ...]


@dataclass(frozen=True)
class FileCheck:
    file_name: str
    source_system: str
    ok: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)
    expected_rows: int | None = None
    actual_rows: int | None = None
    actual_sha256: str | None = None


def load_manifest(batch_dir: Path) -> Manifest:
    path = batch_dir / "manifest.json"
    if not path.exists():
        raise ManifestError(f"MANIFEST_MISSING: no manifest.json in {batch_dir.name}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"MANIFEST_UNPARSEABLE: {batch_dir.name}/manifest.json: {exc}") from exc
    try:
        files = tuple(
            ManifestFile(
                file_name=f["file_name"],
                source_system=f["source_system"],
                row_count=int(f["row_count"]),
                sha256=str(f["sha256"]).lower(),
            )
            for f in doc["files"]
        )
        return Manifest(batch_id=doc["batch_id"], delivered_at=doc["delivered_at"], files=files)
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestError(f"MANIFEST_INVALID: {batch_dir.name}/manifest.json missing field: {exc}") from exc


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_csv_data_rows(path: Path) -> int:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        total = sum(1 for _ in reader)
    return max(0, total - 1)


def validate_batch_files(batch_dir: Path, manifest: Manifest) -> list[FileCheck]:
    checks: list[FileCheck] = []
    for mf in manifest.files:
        path = batch_dir / mf.file_name
        if not path.exists():
            checks.append(
                FileCheck(mf.file_name, mf.source_system, ok=False, reasons=("FILE_MISSING",),
                          expected_rows=mf.row_count)
            )
            continue
        actual_sha = sha256_of_file(path)
        actual_rows = count_csv_data_rows(path)
        reasons: list[str] = []
        if actual_rows != mf.row_count:
            reasons.append(f"ROW_COUNT_MISMATCH expected={mf.row_count} actual={actual_rows}")
        if actual_sha != mf.sha256:
            reasons.append("SHA256_MISMATCH")
        checks.append(
            FileCheck(mf.file_name, mf.source_system, ok=not reasons, reasons=tuple(reasons),
                      expected_rows=mf.row_count, actual_rows=actual_rows, actual_sha256=actual_sha)
        )
    return checks
