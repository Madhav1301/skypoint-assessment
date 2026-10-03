"""Scenario tests for the full row transformation (clean_row).

Each scenario is a realistic raw row shaped like the pack's data; several
reproduce planted traps verbatim. The PHI test asserts the hard boundary:
nothing identifying survives into a CleanedRow.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from pipeline.cleaning import build_context, clean_row
from pipeline.config import load_source_conventions

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRET = "unit-test-secret"


@pytest.fixture(scope="module")
def ctx(value_mappings, facility_aliases, icd10_codes):
    conventions = load_source_conventions(REPO_ROOT / "data" / "candidate_pack")["source_systems"]
    return build_context(
        conventions=conventions,
        facility_aliases_cfg=facility_aliases,
        value_mappings_cfg=value_mappings,
        icd10_codes=icd10_codes,
        patient_key_secret=SECRET,
        delivered_date=date(2025, 1, 6),
    )


def raw_row(**overrides) -> dict:
    base = {
        "source_system": "EPIC_NORTH",
        "source_record_id": "E0001",
        "facility_name": "Lakeshore General Hospital",
        "patient_mrn": "EN1000001",
        "patient_first_name": "Joseph",
        "patient_last_name": "Hoffman",
        "patient_dob": "12/28/1983",
        "patient_sex": "M",
        "patient_zip": "54120",
        "patient_phone": "(414) 555-0151",
        "admit_date": "2024-11-09",
        "discharge_date": "11/11/2024",
        "encounter_type": "Inpatient",
        "attending_npi": "1309114175",
        "attending_provider_name": "Dr. Hannah King",
        "primary_dx_code": "N17.9 - Acute kidney failure, unspecified",
        "chief_complaint": "very little urine output, nausea",
        "payer_name": "BCBS",
        "billed_amount": "$90,406.17",
        "claim_status": "Paid in Full",
        "last_updated_ts": "2024-11-14T05:31:17Z",
    }
    base.update(overrides)
    return base


def test_happy_epic_row(ctx):
    row = clean_row(raw_row(), ctx)
    assert not row.is_quarantined
    assert row.facility_id == "FAC001"
    assert row.admit_date == date(2024, 11, 9)
    assert row.discharge_date == date(2024, 11, 11)
    assert row.length_of_stay_days == 2
    assert (row.admit_year, row.admit_quarter, row.admit_month) == (2024, 4, 11)
    assert row.encounter_type == "INPATIENT"
    assert row.claim_status == "PAID"
    assert row.payer_category == "COMMERCIAL"
    assert row.billed_amount == Decimal("90406.17")
    assert row.attending_npi == "1309114175"
    assert row.primary_dx_code == "N17.9" and row.dx_outcome == "IN_REFERENCE"
    assert row.last_updated_ts_utc == datetime(2024, 11, 14, 5, 31, 17, tzinfo=timezone.utc)
    assert row.age_band == "40-64"
    assert row.zip3 == "541"
    assert row.sex == "M"
    assert row.linkage_scope == "PERSON"


def test_meditech_cents_and_dmy(ctx):
    row = clean_row(raw_row(
        source_system="LEGACY_MEDITECH",
        facility_name="Riverbend CH",
        admit_date="05/03/2024",                    # DMY -> 5 March (the brief's example)
        discharge_date="08/03/2024",
        billed_amount="1845000",                    # cents -> $18,450.00
        last_updated_ts="26/10/2024 07:32:41",      # Chicago CDT -> UTC
        patient_dob="13-06-1992",
    ), ctx)
    assert not row.is_quarantined
    assert row.facility_id == "FAC004"
    assert row.admit_date == date(2024, 3, 5)
    assert row.billed_amount == Decimal("18450.00")
    assert row.last_updated_ts_utc == datetime(2024, 10, 26, 12, 32, 41, tzinfo=timezone.utc)


def test_unresolved_facility_quarantines(ctx):
    for name, system in (
        ("Westfield Surgical Center", "ATHENA_CLINICS"),
        ("TEST FACILITY - DO NOT USE", "EPIC_NORTH"),
    ):
        row = clean_row(raw_row(source_system=system, facility_name=name), ctx)
        assert row.is_quarantined
        assert "UNRESOLVED_FACILITY" in row.errors


def test_missing_timestamp_quarantines(ctx):
    row = clean_row(raw_row(last_updated_ts=""), ctx)
    assert row.is_quarantined
    assert "INVALID_LAST_UPDATED_TS" in row.errors


def test_missing_record_id_quarantines(ctx):
    row = clean_row(raw_row(source_record_id="  "), ctx)
    assert row.is_quarantined
    assert "MISSING_SOURCE_RECORD_ID" in row.errors


def test_future_admit_becomes_null_with_reason_row_kept(ctx):
    row = clean_row(raw_row(admit_date="2031-06-22"), ctx)
    assert not row.is_quarantined                     # warning, not error
    assert row.admit_date is None
    assert row.reasons["admit_date"] == "FUTURE_DATE"
    assert row.age_band == "UNKNOWN"                  # band needs admit date


def test_impossible_and_placeholder_dates(ctx):
    row = clean_row(raw_row(admit_date="2024-02-30", discharge_date="TBD"), ctx)
    assert row.admit_date is None and row.reasons["admit_date"] == "INVALID_DATE"
    assert row.discharge_date is None and row.reasons["discharge_date"] == "NOT_PROVIDED"
    assert row.length_of_stay_days is None


def test_discharge_before_admit_flagged_not_dropped(ctx):
    row = clean_row(raw_row(admit_date="2023-12-17", discharge_date="2023-12-16"), ctx)
    assert not row.is_quarantined
    assert row.discharge_before_admit is True
    assert row.length_of_stay_days is None


def test_unparseable_amount_is_null_never_zero(ctx):
    row = clean_row(raw_row(billed_amount="PENDING"), ctx)
    assert row.billed_amount is None
    assert row.reasons["billed_amount"] == "PENDING"


def test_missing_dob_gets_identity_scoped_key(ctx):
    linked = clean_row(raw_row(), ctx)
    unlinked = clean_row(raw_row(patient_dob=""), ctx)
    assert unlinked.linkage_scope == "IDENTITY"
    assert unlinked.patient_key is not None
    assert unlinked.patient_key != linked.patient_key
    assert unlinked.age_band == "UNKNOWN"
    assert "patient:MISSING_DOB_NO_LINKAGE" in unlinked.warnings


def test_same_person_links_across_systems(ctx):
    epic = clean_row(raw_row(), ctx)
    athena = clean_row(raw_row(
        source_system="ATHENA_CLINICS",
        source_record_id="139999",
        facility_name="Eastgate Urgent Care",
        patient_mrn="AC9999999",                      # different MRN
        patient_first_name="JOSEPH",                  # case noise
        patient_last_name="hoffman",
        last_updated_ts="2024-11-20 10:00:00",
    ), ctx)
    assert epic.patient_key == athena.patient_key     # minimum rule in action


def test_no_phi_survives_into_cleaned_row(ctx):
    raw = raw_row()
    row = clean_row(raw, ctx)
    phi_values = {
        raw["patient_mrn"], raw["patient_first_name"], raw["patient_last_name"],
        raw["patient_dob"], raw["patient_phone"], raw["patient_zip"],
        raw["chief_complaint"],
    }
    for f in dataclasses.fields(row):
        value = getattr(row, f.name)
        payload = str(value)
        for secret_value in phi_values:
            assert secret_value not in payload, f"PHI leaked via field {f.name}"
