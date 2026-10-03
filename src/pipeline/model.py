"""Gold layer (Task 5), required export (Task 7) and output writing.

Reference tables are rebuilt from the reference files on every run and the
gold layer is defined as views over the insert-only clean layer — both are
pure derivations, which is what makes re-runs and batch-by-batch runs land
on identical outputs.
"""

from __future__ import annotations

import json
from pathlib import Path

from .bulk import bulk_insert
from .scd2 import build_provider_scd2, load_roster_snapshots

PROVIDER_COLUMNS = {
    "npi": "VARCHAR",
    "provider_last_name": "VARCHAR",
    "provider_first_name": "VARCHAR",
    "credential": "VARCHAR",
    "specialty": "VARCHAR",
    "primary_facility_id": "VARCHAR",
    "employment_status": "VARCHAR",
    "valid_from": "DATE",
    "valid_to": "DATE",
}

FACILITY_COLUMNS = {
    "facility_id": "VARCHAR",
    "facility_name": "VARCHAR",
    "source_system": "VARCHAR",
    "facility_type": "VARCHAR",
    "city": "VARCHAR",
    "state": "VARCHAR",
    "bed_count": "INTEGER",
    "ownership": "VARCHAR",
    "go_live_date": "DATE",
}


def build_reference(con, data_dir: Path) -> None:
    """ref.facilities, ref.icd10, ref.provider_scd2 — rebuilt idempotently."""
    facilities_doc = json.loads(
        (data_dir / "reference" / "source_systems_and_facilities.json").read_text(encoding="utf-8")
    )
    cols = ",\n".join(f"{n} {t}" for n, t in FACILITY_COLUMNS.items())
    con.execute(f"CREATE OR REPLACE TABLE ref.facilities ({cols})")
    bulk_insert(con, "ref.facilities", FACILITY_COLUMNS, [
        tuple(f[name] for name in FACILITY_COLUMNS) for f in facilities_doc["facilities"]
    ])

    icd10_path = (data_dir / "reference" / "icd10_reference.csv").as_posix()
    con.execute(f"""
        CREATE OR REPLACE TABLE ref.icd10 AS
        SELECT trim(icd10_code) AS icd10_code,
               description,
               category,
               upper(trim(is_chronic)) = 'Y' AS is_chronic
        FROM read_csv('{icd10_path}', header=true, all_varchar=true)
        WHERE trim(icd10_code) <> ''
    """)

    periods = build_provider_scd2(load_roster_snapshots(data_dir / "reference" / "provider_roster"))
    cols = ",\n".join(f"{n} {t}" for n, t in PROVIDER_COLUMNS.items())
    con.execute(f"CREATE OR REPLACE TABLE ref.provider_scd2 ({cols})")
    bulk_insert(con, "ref.provider_scd2", PROVIDER_COLUMNS, [
        (p.npi, p.provider_last_name, p.provider_first_name, p.credential,
         p.specialty, p.primary_facility_id, p.employment_status, p.valid_from, p.valid_to)
        for p in periods
    ])


GOLD_VIEWS = [
    # Facts: full insert-only history, and latest version per encounter.
    """
    CREATE OR REPLACE VIEW gold.fact_encounter_versions AS
    SELECT * FROM clean.encounter_versions
    """,
    """
    CREATE OR REPLACE VIEW gold.fact_encounter_current AS
    SELECT *,
           count(*) OVER (PARTITION BY source_system, source_record_id) AS version_count
    FROM clean.encounter_versions
    QUALIFY row_number() OVER (
        PARTITION BY source_system, source_record_id
        ORDER BY last_updated_ts_utc DESC
    ) = 1
    """,
    # Dimensions.
    """
    CREATE OR REPLACE VIEW gold.dim_provider AS
    SELECT *, valid_to = DATE '9999-12-31' AS is_current FROM ref.provider_scd2
    """,
    "CREATE OR REPLACE VIEW gold.dim_facility AS SELECT * FROM ref.facilities",
    "CREATE OR REPLACE VIEW gold.dim_diagnosis AS SELECT * FROM ref.icd10",
    """
    CREATE OR REPLACE VIEW gold.dim_patient AS
    SELECT patient_key,
           arg_max(sex, (last_updated_ts_utc, source_system, source_record_id)) AS sex,
           arg_max(zip3, (last_updated_ts_utc, source_system, source_record_id)) AS zip3,
           count(DISTINCT source_system) AS source_system_count,
           count(*) AS current_encounter_count
    FROM gold.fact_encounter_current
    WHERE patient_key IS NOT NULL
    GROUP BY patient_key
    """,
    """
    CREATE OR REPLACE VIEW gold.dim_payer AS
    SELECT DISTINCT payer_name_raw AS payer_name, payer_category
    FROM clean.encounter_versions
    """,
    """
    CREATE OR REPLACE VIEW gold.dim_date AS
    SELECT d::DATE                   AS date_key,
           year(d)                   AS year,
           quarter(d)                AS quarter,
           month(d)                  AS month,
           strftime(d, '%Y-%m')      AS year_month
    FROM generate_series(DATE '2022-01-01', DATE '2026-12-31', INTERVAL 1 DAY) t(d)
    """,
    # Task 7 export. All six conditions evaluate fail-closed: a null value
    # fails its condition (joins require resolved facility and chronic dx;
    # predicates require known status, 2024 admit, valid amount >= 5000).
    """
    CREATE OR REPLACE VIEW gold.chronic_acute_encounters AS
    WITH readmit_pool AS (
        SELECT patient_key, source_system, source_record_id, admit_date
        FROM gold.fact_encounter_current
        WHERE encounter_type = 'INPATIENT'
          AND claim_status IS NOT NULL AND claim_status <> 'VOID'
          AND facility_id IS NOT NULL
          AND admit_date IS NOT NULL
          AND patient_key IS NOT NULL
    )
    SELECT
        c.source_system || ':' || c.source_record_id AS encounter_key,
        c.source_system,
        c.source_record_id,
        c.facility_id,
        f.facility_name,
        f.facility_type,
        c.patient_key,
        c.age_band,
        c.sex,
        c.zip3 AS patient_zip3,
        c.admit_date,
        c.discharge_date,
        c.length_of_stay_days,
        c.encounter_type,
        c.primary_dx_code,
        d.description AS dx_description,
        d.category AS chronic_category,
        c.attending_npi,
        p.specialty AS attending_specialty_at_encounter,
        p.employment_status AS attending_employment_status_at_encounter,
        c.payer_category,
        c.billed_amount AS billed_amount_usd,
        c.claim_status,
        CASE WHEN c.encounter_type <> 'INPATIENT' THEN ''
             WHEN c.discharge_date IS NOT NULL AND c.patient_key IS NOT NULL AND EXISTS (
                 SELECT 1 FROM readmit_pool r
                 WHERE r.patient_key = c.patient_key
                   AND NOT (r.source_system = c.source_system
                            AND r.source_record_id = c.source_record_id)
                   AND datediff('day', c.discharge_date, r.admit_date) BETWEEN 1 AND 30
             ) THEN '1'
             ELSE '0'
        END AS readmit_30d_flag,
        c.version_count,
        c.source_batch_id,
        c.source_file_name,
        c.source_row_number
    FROM gold.fact_encounter_current c
    JOIN gold.dim_facility f ON f.facility_id = c.facility_id
    JOIN gold.dim_diagnosis d ON d.icd10_code = c.primary_dx_code AND d.is_chronic
    LEFT JOIN gold.dim_provider p
           ON p.npi = c.attending_npi
          AND c.admit_date >= p.valid_from
          AND c.admit_date < p.valid_to
    WHERE c.claim_status IS NOT NULL AND c.claim_status <> 'VOID'
      AND c.encounter_type IN ('INPATIENT', 'OBSERVATION', 'EMERGENCY')
      AND c.admit_year = 2024
      AND c.billed_amount IS NOT NULL AND c.billed_amount >= 5000.00
    """,
]


def build_gold(con) -> None:
    for stmt in GOLD_VIEWS:
        con.execute(stmt)


# (output_name, source relation, deterministic ORDER BY). quarantined_at and
# audit timestamps are wall-clock, so quarantine exports a stable column list
# and batch_audit is the one output allowed to differ between re-runs.
_EXPORTS = [
    ("fact_encounter_versions", "gold.fact_encounter_versions",
     "source_system, source_record_id, last_updated_ts_utc"),
    ("fact_encounter_current", "gold.fact_encounter_current",
     "source_system, source_record_id"),
    ("dim_provider", "gold.dim_provider", "npi, valid_from"),
    ("dim_patient", "gold.dim_patient", "patient_key"),
    ("dim_facility", "gold.dim_facility", "facility_id"),
    ("dim_diagnosis", "gold.dim_diagnosis", "icd10_code"),
    ("dim_payer", "gold.dim_payer", "payer_name NULLS FIRST, payer_category"),
    ("dim_date", "gold.dim_date", "date_key"),
    ("quarantine",
     "(SELECT batch_id, file_name, source_row_number, source_system, source_record_id, "
     "errors, field_reasons FROM meta.quarantine)",
     "batch_id, file_name, source_row_number"),
    ("batch_audit", "meta.batch_audit", "batch_id, file_name NULLS FIRST"),
    ("dq_results", "meta.dq_results", "batch_id, severity, check_id"),
    ("chronic_acute_encounters", "gold.chronic_acute_encounters",
     "admit_date, source_system, source_record_id"),
]


def write_outputs(con, output_dir: Path) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, relation, order_by in _EXPORTS:
        path = (output_dir / f"{name}.csv").as_posix()
        con.execute(
            f"COPY (SELECT * FROM {relation} ORDER BY {order_by}) TO '{path}' (HEADER, DELIMITER ',')"
        )
        written.append(f"{name}.csv")
    written.extend(write_dq_reports(con, output_dir))
    return written


def write_dq_reports(con, output_dir: Path) -> list[str]:
    """One Markdown DQ report per batch: status, reconciliation, checks.

    Deliberately free of wall-clock values so re-runs produce identical files.
    """
    written = []
    batches = [r[0] for r in con.execute(
        "SELECT DISTINCT batch_id FROM meta.batch_audit ORDER BY batch_id"
    ).fetchall()]
    for batch_id in batches:
        summary = con.execute(
            "SELECT status, reason, rows_received, rows_loaded, rows_duplicate, "
            "rows_stale, rows_quarantined, delivered_at FROM meta.batch_audit "
            "WHERE batch_id = ? AND file_name IS NULL", [batch_id]
        ).fetchone()
        files = con.execute(
            "SELECT file_name, status, reason, rows_received, rows_loaded, rows_duplicate, "
            "rows_stale, rows_quarantined FROM meta.batch_audit "
            "WHERE batch_id = ? AND file_name IS NOT NULL ORDER BY file_name", [batch_id]
        ).fetchall()
        checks = con.execute(
            "SELECT severity, check_id, rows_flagged FROM meta.dq_results "
            "WHERE batch_id = ? ORDER BY severity, check_id", [batch_id]
        ).fetchall()

        status, reason, received, loaded, dup, stale, quarantined, delivered = summary
        lines = [
            f"# Data quality report — {batch_id}",
            "",
            f"- **Status:** {status}",
            f"- **Delivered at:** {delivered or 'n/a'}",
        ]
        if reason:
            lines.append(f"- **Reason:** {reason}")
        if received is not None:
            lines += [
                "",
                "## Row reconciliation",
                "",
                "`received = new versions + duplicates + stale + quarantined`",
                "",
                "| File | Received | New versions | Duplicates | Stale | Quarantined |",
                "|---|---:|---:|---:|---:|---:|",
            ]
            for f_name, f_status, _f_reason, f_rec, f_load, f_dup, f_stale, f_quar in files:
                lines.append(
                    f"| {f_name} ({f_status}) | {f_rec or 0} | {f_load or 0} | "
                    f"{f_dup or 0} | {f_stale or 0} | {f_quar or 0} |"
                )
            lines.append(
                f"| **Total** | **{received or 0}** | **{loaded or 0}** | **{dup or 0}** | "
                f"**{stale or 0}** | **{quarantined or 0}** |"
            )
        if checks:
            lines += [
                "",
                "## Checks",
                "",
                "| Severity | Check | Rows flagged |",
                "|---|---|---:|",
            ]
            for severity, check_id, rows_flagged in checks:
                lines.append(f"| {severity} | {check_id} | {rows_flagged} |")
        if status == "REJECTED":
            lines += ["", "_Batch rejected before load: no rows entered any modeled table._"]
        if status == "GATE_FAILED":
            lines += ["", "_Publish gate failed: nothing from this batch was published._"]
        lines.append("")

        name = f"dq_report_{batch_id}.md"
        (output_dir / name).write_text("\n".join(lines), encoding="utf-8")
        written.append(name)
    return written
