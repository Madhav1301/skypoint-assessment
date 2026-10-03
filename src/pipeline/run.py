"""Single entry point: process all pending batches in order, rebuild the
gold layer, write every output CSV and the per-batch DQ reports.

A rejected or gate-failed batch is reported in the audit, never fatal: the
run exits 0 unless the pipeline itself crashes.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import db
from . import logging_setup as log
from .config import (
    Settings,
    load_icd10_reference,
    load_source_conventions,
    load_yaml_config,
    settings_from_env,
)
from .ingest import RunConfig, process_batch
from .model import build_gold, build_reference, write_outputs
from .versioning import VersionState


def discover_batches(data_dir: Path) -> list[Path]:
    landing = data_dir / "landing"
    if not landing.exists():
        return []
    return sorted(p for p in landing.iterdir() if p.is_dir() and p.name.startswith("batch_"))


def build_run_config(settings: Settings) -> RunConfig:
    conventions = load_source_conventions(settings.data_dir)["source_systems"]
    return RunConfig(
        contracts=load_yaml_config(settings.config_dir, "schema_contracts.yaml")["contracts"],
        conventions=conventions,
        facility_aliases_cfg=load_yaml_config(settings.config_dir, "facility_aliases.yaml"),
        value_mappings_cfg=load_yaml_config(settings.config_dir, "value_mappings.yaml"),
        icd10_codes=set(load_icd10_reference(settings.data_dir).keys()),
        patient_key_secret=settings.patient_key_secret,
        gate_threshold=settings.publish_gate_threshold,
    )


def main() -> int:
    settings = settings_from_env()
    log.setup_logging(settings.log_level)
    log.info("pipeline starting", data_dir=str(settings.data_dir), db_path=str(settings.db_path))

    if not settings.patient_key_secret:
        log.error("PATIENT_KEY_SECRET is not set; refusing to run (patient keys must be reproducible)")
        return 1
    batches = discover_batches(settings.data_dir)
    if not batches:
        log.error("no batch directories found", data_dir=str(settings.data_dir))
        return 1

    cfg = build_run_config(settings)
    con = db.connect(settings.db_path)
    try:
        state = VersionState.load(con)
        results = {}
        for batch_dir in batches:
            results[batch_dir.name] = process_batch(con, batch_dir, cfg, state)

        build_reference(con, settings.data_dir)
        build_gold(con)
        written = write_outputs(con, settings.output_dir)
        log.info("outputs written", output_dir=str(settings.output_dir), files=len(written))
        log.info("pipeline finished", results=results)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
