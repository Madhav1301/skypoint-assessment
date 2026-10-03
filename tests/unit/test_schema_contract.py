"""Unit tests for schema-contract matching."""

from __future__ import annotations

import pytest

from pipeline.ingest import CANONICAL_COLUMNS
from pipeline.schema_contract import SchemaContractError, match_contract

ATHENA_V2_HEADER = [
    "source_system", "source_record_id", "facility_name", "encounter_source",
    "admit_date", "discharge_date", "encounter_type", "patient_mrn",
    "patient_last_name", "patient_first_name", "patient_dob", "patient_sex",
    "patient_zip", "patient_phone", "attending_provider_npi",
    "attending_provider_name", "primary_dx_code", "chief_complaint",
    "payer_name", "total_charge", "claim_status", "last_updated_ts",
]


def test_epic_v1_matches_identity(contracts):
    match = match_contract(contracts, "EPIC_NORTH", list(CANONICAL_COLUMNS))
    assert match.version == "v1"
    assert match.extra_columns == ()
    assert match.canonical_by_source["billed_amount"] == "billed_amount"


def test_athena_v2_matches_with_renames_and_extras(contracts):
    match = match_contract(contracts, "ATHENA_CLINICS", ATHENA_V2_HEADER)
    assert match.version == "v2"
    assert match.canonical_by_source["attending_provider_npi"] == "attending_npi"
    assert match.canonical_by_source["total_charge"] == "billed_amount"
    assert match.extra_columns == ("encounter_source",)
    # Every mapped target is a canonical column and all 21 are covered.
    assert sorted(match.canonical_by_source.values()) == sorted(CANONICAL_COLUMNS)


def test_reordered_header_is_rejected(contracts):
    header = list(CANONICAL_COLUMNS)
    header[0], header[1] = header[1], header[0]
    with pytest.raises(SchemaContractError, match="reordered"):
        match_contract(contracts, "EPIC_NORTH", header)


def test_unexpected_column_is_rejected_with_hint(contracts):
    header = list(CANONICAL_COLUMNS) + ["surprise_column"]
    with pytest.raises(SchemaContractError, match="surprise_column"):
        match_contract(contracts, "EPIC_NORTH", header)


def test_unknown_source_system_is_rejected(contracts):
    with pytest.raises(SchemaContractError, match="no contract configured"):
        match_contract(contracts, "MYSTERY_EHR", list(CANONICAL_COLUMNS))
