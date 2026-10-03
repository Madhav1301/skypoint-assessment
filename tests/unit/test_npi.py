"""NPI parser tests — the four planted invalid NPIs and real formatting noise."""

import pytest

from pipeline.parsers.npi import clean_npi, luhn_80840_valid


@pytest.mark.parametrize("raw,expected", [
    ("1440029787", "1440029787"),
    ("1608565176.0", "1608565176"),   # Excel float artefact
    (" 1228088641 ", "1228088641"),
])
def test_valid_npis_after_cleaning(raw, expected):
    value, reason = clean_npi(raw)
    assert reason is None
    assert value == expected


@pytest.mark.parametrize("bad", [
    # The four planted check-digit corruptions found by profiling the pack
    "1028382221", "1615846455", "1764529126", "1895579909",
])
def test_planted_luhn_failures(bad):
    value, reason = clean_npi(bad)
    assert value is None
    assert reason == "INVALID_NPI_LUHN"
    assert not luhn_80840_valid(bad)


def test_roster_counterparts_of_planted_failures_are_valid():
    for good in ("1028382228", "1615846452", "1764529123", "1895579906"):
        assert luhn_80840_valid(good)


@pytest.mark.parametrize("raw,reason", [
    ("", "MISSING"),
    (None, "MISSING"),
    ("N/A", "NOT_PROVIDED"),
    ("NPI-122983", "INVALID_NPI_FORMAT"),  # too few digits after cleaning
    ("12345", "INVALID_NPI_FORMAT"),
])
def test_invalid_formats(raw, reason):
    value, got = clean_npi(raw)
    assert value is None
    assert got == reason


def test_valid_but_unrostered_is_still_structurally_valid():
    # Distinguishing valid-vs-unrostered happens at the roster join, not here.
    value, reason = clean_npi("1166323372")
    assert value == "1166323372"
    assert reason is None
