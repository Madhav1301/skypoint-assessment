"""PHI primitive tests: age bands, zip3, linkage match key, HMAC patient_key."""

from datetime import date

import pytest

from pipeline.phi import (
    age_band_at,
    first_given_name,
    match_key,
    normalize_name_part,
    patient_key,
    zip3,
)

SECRET = "unit-test-secret"


class TestAgeBand:
    @pytest.mark.parametrize("dob,admit,expected", [
        (date(2010, 6, 1), date(2024, 6, 1), "0-17"),    # 14
        (date(2006, 6, 2), date(2024, 6, 1), "0-17"),    # 17 (birthday tomorrow)
        (date(2006, 6, 1), date(2024, 6, 1), "18-39"),   # 18 today
        (date(1985, 1, 1), date(2024, 6, 1), "18-39"),   # 39
        (date(1984, 5, 1), date(2024, 6, 1), "40-64"),   # 40
        (date(1960, 1, 1), date(2024, 6, 1), "40-64"),   # 64
        (date(1959, 6, 1), date(2024, 6, 1), "65+"),     # 65 today
        (date(1932, 1, 24), date(2024, 8, 19), "65+"),
    ])
    def test_boundaries(self, dob, admit, expected):
        assert age_band_at(dob, admit) == expected

    def test_unknown_when_either_side_missing_or_impossible(self):
        assert age_band_at(None, date(2024, 1, 1)) == "UNKNOWN"
        assert age_band_at(date(1980, 1, 1), None) == "UNKNOWN"
        assert age_band_at(date(2030, 1, 1), date(2024, 1, 1)) == "UNKNOWN"  # dob after admit


class TestZip3:
    def test_valid(self):
        assert zip3("54120") == ("541", None)
        assert zip3("53788-1234") == ("537", None)

    def test_invalid(self):
        assert zip3("ABCDE") == (None, "INVALID_ZIP")
        assert zip3("1234") == (None, "INVALID_ZIP")
        assert zip3("") == (None, "MISSING")


class TestMatchKey:
    def test_normalisation_strips_case_punctuation_middle_initials(self):
        a = match_key("Van Dyke", "Noah J", date(1932, 1, 24), "M")
        b = match_key("VAN DYKE", "NOAH", date(1932, 1, 24), "M")
        c = match_key("van-dyke", "noah  ", date(1932, 1, 24), "M")
        assert a == b == c == "VANDYKE|NOAH|1932-01-24|M"

    def test_no_dob_means_no_link(self):
        assert match_key("Becker", "Noah", None, "M") is None

    def test_unknown_sex_means_no_link(self):
        assert match_key("Becker", "Noah", date(1980, 1, 1), "UNKNOWN") is None

    def test_name_part_helpers(self):
        assert normalize_name_part("O'Brien") == "OBRIEN"
        assert first_given_name("Mary Jo Anne") == "MARY"
        assert first_given_name("  ") is None


class TestPatientKey:
    def test_deterministic_and_key_dependent(self):
        mk = match_key("Becker", "Noah", date(1932, 1, 24), "M")
        k1 = patient_key(SECRET, match=mk, source_system="EPIC_NORTH", mrn="EN1")
        k2 = patient_key(SECRET, match=mk, source_system="ATHENA_CLINICS", mrn="AC9")
        k3 = patient_key("other-secret", match=mk, source_system="EPIC_NORTH", mrn="EN1")
        assert k1 == k2                   # same person across systems -> same key
        assert k1 != k3                   # different secret -> different key
        assert len(k1) == 32 and int(k1, 16) >= 0

    def test_unlinked_identities_stay_per_system(self):
        k_epic = patient_key(SECRET, match=None, source_system="EPIC_NORTH", mrn="X1")
        k_athena = patient_key(SECRET, match=None, source_system="ATHENA_CLINICS", mrn="X1")
        assert k_epic != k_athena         # same MRN text, different systems

    def test_domain_separation_between_namespaces(self):
        # A crafted MRN can never collide with a person-scoped key.
        k_person = patient_key(SECRET, match="A|B|2000-01-01|F", source_system="S", mrn=None)
        k_identity = patient_key(SECRET, match=None, source_system="S", mrn="A|B|2000-01-01|F")
        assert k_person != k_identity

    def test_missing_secret_refuses(self):
        with pytest.raises(ValueError, match="PATIENT_KEY_SECRET"):
            patient_key("", match="A|B|2000-01-01|F", source_system="S", mrn="M")

    def test_no_identity_at_all(self):
        assert patient_key(SECRET, match=None, source_system="S", mrn="  ") is None
