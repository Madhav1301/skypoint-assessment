"""Diagnosis-code tests against the real ICD-10 reference file."""

import pytest

from pipeline.parsers.dx import normalize_dx


@pytest.mark.parametrize("raw,expected_code", [
    ("I10", "I10"),
    ("N39.0", "N39.0"),
    ("e11.65", "E11.65"),                 # lowercase (104 rows in the pack)
    ("E1165", "E11.65"),                  # dotless
    ("E119", "E11.9"),                    # dotless, judgment call: ICD-10 not ICD-9 E-code
    ("M170", "M17.0"),
    ("M5450", "M54.50"),
    ("S93401A", "S93.401A"),
    (" Z00.00 ", "Z00.00"),               # padded
    ("Z23 ", "Z23"),                      # 3 chars, no dot needed
    ("U07.1 ", "U07.1"),
    ("I50.22 - Chronic systolic (congestive) heart failure", "I50.22"),
    ("J44.1 - Chronic obstructive pulmonary disease with (acute) exacerbation", "J44.1"),
])
def test_in_reference_after_normalisation(raw, expected_code, icd10_codes):
    code, outcome = normalize_dx(raw, icd10_codes)
    assert outcome == "IN_REFERENCE"
    assert code == expected_code


def test_valid_format_not_in_reference(icd10_codes):
    code, outcome = normalize_dx("E78.5", icd10_codes)  # hyperlipidaemia, absent from reference
    assert outcome == "VALID_FORMAT_NOT_IN_REFERENCE"
    assert code == "E78.5"


@pytest.mark.parametrize("raw", ["250.00", "401.9", "496"])
def test_planted_icd9_codes(raw, icd10_codes):
    code, outcome = normalize_dx(raw, icd10_codes)
    assert outcome == "ICD9_LEGACY"
    assert code is None  # never guessed into ICD-10


@pytest.mark.parametrize("raw,outcome", [
    ("UNKNOWN", "UNPARSEABLE"),
    ("N/A", "UNPARSEABLE"),
    ("!!!", "UNPARSEABLE"),
    ("", "MISSING"),
    (None, "MISSING"),
])
def test_unparseable_and_missing(raw, outcome, icd10_codes):
    code, got = normalize_dx(raw, icd10_codes)
    assert code is None
    assert got == outcome
