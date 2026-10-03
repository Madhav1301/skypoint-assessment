"""DuckDB connection, schema DDL and column specifications.

Layers as schemas: raw (verbatim + lineage, the only place PHI may exist),
clean (one row per distinct version, de-identified), ref (reference data),
gold (dimensional model), meta (audit / quarantine / DQ observability).
"""

from __future__ import annotations

from pathlib import Path

import duckdb

CANONICAL_RAW_FIELDS = [
    "source_system", "source_record_id", "facility_name", "patient_mrn",
    "patient_first_name", "patient_last_name", "patient_dob", "patient_sex",
    "patient_zip", "patient_phone", "admit_date", "discharge_date",
    "encounter_type", "attending_npi", "attending_provider_name",
    "primary_dx_code", "chief_complaint", "payer_name", "billed_amount",
    "claim_status", "last_updated_ts",
]

RAW_COLUMNS: dict[str, str] = {
    "batch_id": "VARCHAR",
    "file_name": "VARCHAR",
    "source_row_number": "INTEGER",
    "file_sha256": "VARCHAR",
    "ingested_at": "TIMESTAMPTZ",
    **{name: "VARCHAR" for name in CANONICAL_RAW_FIELDS},
    "extras_json": "VARCHAR",
}

# One row per distinct accepted version; insert-only. No PHI by construction:
# raw values are kept beside cleaned values for non-PHI fields only.
CLEAN_COLUMNS: dict[str, str] = {
    "source_batch_id": "VARCHAR",
    "source_file_name": "VARCHAR",
    "source_row_number": "INTEGER",
    "source_system": "VARCHAR",
    "source_record_id": "VARCHAR",
    "last_updated_ts_utc": "TIMESTAMPTZ",
    "last_updated_ts_raw": "VARCHAR",
    "facility_id": "VARCHAR",
    "facility_name_raw": "VARCHAR",
    "patient_key": "VARCHAR",
    "linkage_scope": "VARCHAR",
    "age_band": "VARCHAR",
    "sex": "VARCHAR",
    "zip3": "VARCHAR",
    "admit_date": "DATE",
    "admit_date_raw": "VARCHAR",
    "discharge_date": "DATE",
    "discharge_date_raw": "VARCHAR",
    "discharge_before_admit": "BOOLEAN",
    "length_of_stay_days": "INTEGER",
    "admit_year": "INTEGER",
    "admit_quarter": "INTEGER",
    "admit_month": "INTEGER",
    "encounter_type": "VARCHAR",
    "encounter_type_raw": "VARCHAR",
    "claim_status": "VARCHAR",
    "claim_status_raw": "VARCHAR",
    "payer_category": "VARCHAR",
    "payer_name_raw": "VARCHAR",
    "billed_amount": "DECIMAL(12,2)",
    "billed_amount_raw": "VARCHAR",
    "attending_npi": "VARCHAR",
    "attending_npi_raw": "VARCHAR",
    "attending_provider_name_raw": "VARCHAR",
    "primary_dx_code": "VARCHAR",
    "primary_dx_raw": "VARCHAR",
    "dx_outcome": "VARCHAR",
    "warnings": "VARCHAR",
}

QUARANTINE_COLUMNS: dict[str, str] = {
    "batch_id": "VARCHAR",
    "file_name": "VARCHAR",
    "source_row_number": "INTEGER",
    "source_system": "VARCHAR",
    "source_record_id": "VARCHAR",
    "errors": "VARCHAR",
    "field_reasons": "VARCHAR",
    "quarantined_at": "TIMESTAMPTZ",
}


def _create_table_sql(table: str, columns: dict[str, str], primary_key: str | None = None) -> str:
    cols = ",\n        ".join(f"{name} {dtype}" for name, dtype in columns.items())
    pk = f",\n        PRIMARY KEY ({primary_key})" if primary_key else ""
    return f"CREATE TABLE IF NOT EXISTS {table} (\n        {cols}{pk}\n    )"


DDL = [
    "CREATE SCHEMA IF NOT EXISTS raw",
    "CREATE SCHEMA IF NOT EXISTS clean",
    "CREATE SCHEMA IF NOT EXISTS ref",
    "CREATE SCHEMA IF NOT EXISTS gold",
    "CREATE SCHEMA IF NOT EXISTS meta",
    _create_table_sql("raw.encounters", RAW_COLUMNS,
                      "batch_id, file_name, source_row_number"),
    _create_table_sql("clean.encounter_versions", CLEAN_COLUMNS,
                      "source_system, source_record_id, last_updated_ts_utc"),
    _create_table_sql("meta.quarantine", QUARANTINE_COLUMNS),
    """
    CREATE TABLE IF NOT EXISTS meta.batch_audit (
        batch_id        VARCHAR NOT NULL,
        file_name       VARCHAR,
        source_system   VARCHAR,
        status          VARCHAR NOT NULL,
        reason          VARCHAR,
        rows_received   INTEGER,
        rows_loaded     INTEGER,
        rows_duplicate  INTEGER,
        rows_stale      INTEGER,
        rows_quarantined INTEGER,
        delivered_at    VARCHAR,
        started_at      TIMESTAMPTZ,
        finished_at     TIMESTAMPTZ,
        duration_ms     INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta.dq_results (
        batch_id     VARCHAR NOT NULL,
        check_id     VARCHAR NOT NULL,
        severity     VARCHAR NOT NULL,
        rows_flagged INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta.processed_batches (
        batch_id        VARCHAR PRIMARY KEY,
        manifest_sha256 VARCHAR NOT NULL,
        status          VARCHAR NOT NULL,
        delivered_at    VARCHAR,
        processed_at    TIMESTAMPTZ NOT NULL
    )
    """,
]


def connect(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    init_schema(con)
    return con


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    for stmt in DDL:
        con.execute(stmt)
