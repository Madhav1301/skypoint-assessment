"""DuckDB connection and schema DDL for the raw and meta layers."""

from __future__ import annotations

from pathlib import Path

import duckdb

DDL = [
    "CREATE SCHEMA IF NOT EXISTS raw",
    "CREATE SCHEMA IF NOT EXISTS meta",
    """
    CREATE TABLE IF NOT EXISTS raw.encounters (
        batch_id            VARCHAR NOT NULL,
        file_name           VARCHAR NOT NULL,
        source_row_number   INTEGER NOT NULL,
        file_sha256         VARCHAR NOT NULL,
        ingested_at         TIMESTAMPTZ NOT NULL,
        source_system       VARCHAR,
        source_record_id    VARCHAR,
        facility_name       VARCHAR,
        patient_mrn         VARCHAR,
        patient_first_name  VARCHAR,
        patient_last_name   VARCHAR,
        patient_dob         VARCHAR,
        patient_sex         VARCHAR,
        patient_zip         VARCHAR,
        patient_phone       VARCHAR,
        admit_date          VARCHAR,
        discharge_date      VARCHAR,
        encounter_type      VARCHAR,
        attending_npi       VARCHAR,
        attending_provider_name VARCHAR,
        primary_dx_code     VARCHAR,
        chief_complaint     VARCHAR,
        payer_name          VARCHAR,
        billed_amount       VARCHAR,
        claim_status        VARCHAR,
        last_updated_ts     VARCHAR,
        extras_json         VARCHAR,
        PRIMARY KEY (batch_id, file_name, source_row_number)
    )
    """,
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
