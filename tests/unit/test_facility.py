"""Facility resolution tests against the real alias table."""

import pytest

from pipeline.parsers.facility import build_alias_lookup, normalize_facility_name, resolve_facility


@pytest.fixture(scope="module")
def lookup(facility_aliases):
    return build_alias_lookup(facility_aliases)


@pytest.mark.parametrize("raw,system,expected", [
    # Real variants observed in the pack, including case and padding noise
    ("Lakeshore General Hospital", "EPIC_NORTH", "FAC001"),
    ("lakeshore general hospital ", "EPIC_NORTH", "FAC001"),
    ("LAKESHORE GENERAL HOSPITAL", "EPIC_NORTH", "FAC001"),
    ("Lakeshore Gen Hosp", "EPIC_NORTH", "FAC001"),
    ("St. Brendan Medical Center", "EPIC_NORTH", "FAC002"),
    ("ST BRENDAN MED CTR", "EPIC_NORTH", "FAC002"),
    ("St. Brendan's Medical Center", "EPIC_NORTH", "FAC002"),
    ("Saint Brendan Medical Center", "EPIC_NORTH", "FAC002"),
    ("MAPLE GROVE PEDS", "EPIC_NORTH", "FAC003"),
    ("Maple Grove Pediatrics ", "EPIC_NORTH", "FAC003"),
    ("Riverbend CH", "LEGACY_MEDITECH", "FAC004"),
    ("Riverbend Community Hosp.", "LEGACY_MEDITECH", "FAC004"),
    ("RIVERBEND COMM HOSP", "LEGACY_MEDITECH", "FAC004"),
    ("Harborpoint Behavioral Health", "LEGACY_MEDITECH", "FAC005"),
    ("HARBOR PT BEHAVIORAL HLTH", "LEGACY_MEDITECH", "FAC005"),
    ("Harbor Point BH", "LEGACY_MEDITECH", "FAC005"),
    ("Cedar Vly Family Clinic", "ATHENA_CLINICS", "FAC006"),
    ("Cedar Valley Clinic", "ATHENA_CLINICS", "FAC006"),
    ("EASTGATE UC", "ATHENA_CLINICS", "FAC007"),
    ("East Gate Urgent Care", "ATHENA_CLINICS", "FAC007"),
    ("SUMMIT RIDGE ORTHOPAEDICS", "ATHENA_CLINICS", "FAC008"),
    ("Summit Ridge Orthopedic Clinic", "ATHENA_CLINICS", "FAC008"),
])
def test_known_variants_resolve(raw, system, expected, lookup):
    facility_id, reason = resolve_facility(raw, system, lookup)
    assert reason is None
    assert facility_id == expected


@pytest.mark.parametrize("raw,system", [
    ("TEST FACILITY - DO NOT USE", "EPIC_NORTH"),       # planted trap
    ("Westfield Surgical Center", "ATHENA_CLINICS"),    # not in the 8-facility master
    ("Lakeshore General Hospital", "ATHENA_CLINICS"),   # right name, wrong system
])
def test_unresolved_goes_to_quarantine_never_guessed(raw, system, lookup):
    facility_id, reason = resolve_facility(raw, system, lookup)
    assert facility_id is None
    assert reason == "UNRESOLVED_FACILITY"


def test_missing_name(lookup):
    assert resolve_facility("", "EPIC_NORTH", lookup) == (None, "MISSING")


def test_normalisation_rules():
    assert normalize_facility_name(" st. brendan's  medical-center ") == "ST BRENDANS MEDICAL CENTER"
