"""Single entry point: process all pending batches in order, export outputs.

A rejected batch is reported in the audit, never fatal: the run exits 0
unless the pipeline itself crashes.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import db
from . import logging_setup as log
from .config import load_yaml_config, settings_from_env
from .ingest import process_batch


def discover_batches(data_dir: Path) -> list[Path]:
    landing = data_dir / "landing"
    if not landing.exists():
        return []
    return sorted(p for p in landing.iterdir() if p.is_dir() and p.name.startswith("batch_"))


def export_outputs(con, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_csv = (output_dir / "batch_audit.csv").as_posix()
    con.execute(
        f"COPY (SELECT * FROM meta.batch_audit ORDER BY batch_id, file_name NULLS FIRST) "
        f"TO '{audit_csv}' (HEADER, DELIMITER ',')"
    )


def main() -> int:
    settings = settings_from_env()
    log.setup_logging(settings.log_level)
    log.info("pipeline starting", data_dir=str(settings.data_dir), db_path=str(settings.db_path))

    batches = discover_batches(settings.data_dir)
    if not batches:
        log.error("no batch directories found", data_dir=str(settings.data_dir))
        return 1

    contracts = load_yaml_config(settings.config_dir, "schema_contracts.yaml")["contracts"]
    con = db.connect(settings.db_path)
    try:
        results = {}
        for batch_dir in batches:
            results[batch_dir.name] = process_batch(con, batch_dir, contracts)
        export_outputs(con, settings.output_dir)
        log.info("pipeline finished", results=results)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
