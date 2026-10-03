"""Categorical mapping tests against the real value_mappings.yaml."""

import pytest

from pipeline.parsers.categorical import (
    build_lookup,
    map_claim_status,
    map_encounter_type,
    map_payer_category,
    map_sex,
)


@pytest.fixture(scope="module")
def lookups(value_mappings):
    return {name: build_lookup(mapping) for name, mapping in value_mappings.items()}


@pytest.mark.parametrize("raw,expected", [
    ("Inpatient", "INPATIENT"), ("IP", "INPATIENT"), ("INPT", "INPATIENT"),
    ("IN-PATIENT", "INPATIENT"), ("inpatient admission", "INPATIENT"),
    ("OBS", "OBSERVATION"), ("Obs Stay", "OBSERVATION"),
    ("ED", "EMERGENCY"), ("ER", "EMERGENCY"), ("EMERGENCY DEPT", "EMERGENCY"),
    ("Emergency Room", "EMERGENCY"),
    ("OP", "OUTPATIENT"), ("Out Patient", "OUTPATIENT"), ("Office Visit", "OUTPATIENT"),
    ("Clinic Visit", "OUTPATIENT"),
    ("TELEMED", "TELEHEALTH"), ("Video Visit", "TELEHEALTH"), ("Virtual", "TELEHEALTH"),
    ("UC", "URGENT_CARE"), ("Walk-in Urgent", "URGENT_CARE"),
])
def test_encounter_type_variants(raw, expected, lookups):
    value, reason = map_encounter_type(raw, lookups["encounter_type"])
    assert value == expected
    assert reason is None


def test_encounter_type_unknown_and_missing(lookups):
    assert map_encounter_type("OTHER", lookups["encounter_type"]) == ("UNKNOWN", "UNMAPPED_ENCOUNTER_TYPE")
    assert map_encounter_type("", lookups["encounter_type"]) == ("UNKNOWN", "MISSING")


@pytest.mark.parametrize("raw,expected", [
    ("PAID", "PAID"), ("Paid in Full", "PAID"), ("Closed - Paid", "PAID"),
    ("DENIED", "DENIED"), ("Rejected", "DENIED"),
    ("Submitted", "SUBMITTED"), ("Pending", "SUBMITTED"), ("In Process", "SUBMITTED"),
    ("Void", "VOID"), ("VOIDED", "VOID"),
    ("Cancelled", "VOID"),  # assumption A2
])
def test_claim_status_variants(raw, expected, lookups):
    value, reason = map_claim_status(raw, lookups["claim_status"])
    assert value == expected
    assert reason is None


def test_claim_status_fails_closed(lookups):
    # No UNKNOWN bucket: unmapped/missing stays null so export filters fail closed.
    assert map_claim_status("Mystery", lookups["claim_status"]) == (None, "UNMAPPED_CLAIM_STATUS")
    assert map_claim_status("", lookups["claim_status"]) == (None, "MISSING")


@pytest.mark.parametrize("raw,expected", [
    ("Medicare", "MEDICARE"), ("MCR", "MEDICARE"), ("MEDICARE PART A", "MEDICARE"),
    ("Medicare - Part B", "MEDICARE"),
    ("MEDICAID", "MEDICAID"), ("IA Medicaid", "MEDICAID"),
    ("State Medicaid Plan", "MEDICAID"), ("BadgerCare Plus", "MEDICAID"),
    ("Aetna", "COMMERCIAL"), ("AETNA INC", "COMMERCIAL"), ("CIGNA HEALTH", "COMMERCIAL"),
    ("UnitedHealthcare", "COMMERCIAL"), ("UHC", "COMMERCIAL"), ("BCBS", "COMMERCIAL"),
    ("Blue Cross Blue Shield", "COMMERCIAL"),
    ("Self Pay", "SELF_PAY"), ("self-pay", "SELF_PAY"), ("SELFPAY", "SELF_PAY"),
    ("Uninsured", "SELF_PAY"),
    ("Tricare", "OTHER"), ("Workers Comp", "OTHER"),
])
def test_payer_category_variants(raw, expected, lookups):
    value, reason = map_payer_category(raw, lookups["payer_category"])
    assert value == expected
    assert reason is None


def test_payer_unknowns(lookups):
    assert map_payer_category("N/A", lookups["payer_category"]) == ("UNKNOWN", "UNMAPPED_PAYER")
    assert map_payer_category("", lookups["payer_category"]) == ("UNKNOWN", "MISSING")


@pytest.mark.parametrize("raw,expected", [
    ("M", "M"), ("Male", "M"), ("F", "F"), ("Female", "F"), ("FEMALE", "F"),
])
def test_sex_variants(raw, expected, lookups):
    assert map_sex(raw, lookups["patient_sex"])[0] == expected


def test_sex_missing(lookups):
    assert map_sex("", lookups["patient_sex"]) == ("UNKNOWN", "MISSING")
