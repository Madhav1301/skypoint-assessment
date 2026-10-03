"""Settings from environment variables and loaders for config / reference files."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    output_dir: Path
    config_dir: Path
    db_path: Path
    patient_key_secret: str
    publish_gate_threshold: float
    log_level: str


def settings_from_env() -> Settings:
    data_dir = Path(os.environ.get("DATA_DIR", "data/candidate_pack"))
    output_dir = Path(os.environ.get("OUTPUT_DIR", "output"))
    config_dir = Path(os.environ.get("CONFIG_DIR", "config"))
    db_path = Path(os.environ.get("DB_PATH", str(output_dir / "warehouse.duckdb")))
    return Settings(
        data_dir=data_dir,
        output_dir=output_dir,
        config_dir=config_dir,
        db_path=db_path,
        patient_key_secret=os.environ.get("PATIENT_KEY_SECRET", ""),
        publish_gate_threshold=float(os.environ.get("PUBLISH_GATE_THRESHOLD", "0.10")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )


def load_yaml_config(config_dir: Path, name: str) -> dict:
    path = config_dir / name
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_source_conventions(data_dir: Path) -> dict:
    """Parse reference/source_systems_and_facilities.json.

    Returns {"source_systems": {name: conventions}, "facilities": [...]}.
    """
    path = data_dir / "reference" / "source_systems_and_facilities.json"
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    systems = {s["source_system"]: s for s in doc["source_systems"]}
    return {"source_systems": systems, "facilities": doc["facilities"]}
