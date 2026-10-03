"""Amount parser tests — inputs are real values observed in the data pack."""

from decimal import Decimal

import pytest

from pipeline.parsers.amounts import parse_amount


@pytest.mark.parametrize("raw,expected", [
    ("$1,089.88", "1089.88"),
    ("$90,406.17", "90406.17"),
    ("245.24", "245.24"),
    ("USD 348.11", "348.11"),
    ("145.85 USD", "145.85"),
    ("$ 357.84", "357.84"),
    ("$ 67986.56", "67986.56"),
    ("1,381.39 USD", "1381.39"),
    ("$383.06 ", "383.06"),
    ("$ 107.1", "107.10"),
    ("$2.07K", "2070.00"),
    ("$67.53K", "67530.00"),
])
def test_usd_formats(raw, expected):
    value, reason = parse_amount(raw, "USD")
    assert reason is None
    assert value == Decimal(expected)


@pytest.mark.parametrize("raw,expected", [
    # The brief's own worked example: 1845000 cents = $18,450.00
    ("1845000", "18450.00"),
    ("135890", "1358.90"),
    ("2043523", "20435.23"),
    (" 642685", "6426.85"),
    ("1,683,845", "16838.45"),
    ("15,172", "151.72"),
    ("2818999.0", "28189.99"),
    ("6797", "67.97"),
])
def test_meditech_cents(raw, expected):
    value, reason = parse_amount(raw, "USD_CENTS")
    assert reason is None
    assert value == Decimal(expected)


@pytest.mark.parametrize("raw,reason", [
    ("", "MISSING"),
    ("   ", "MISSING"),
    (None, "MISSING"),
    ("N/A", "NOT_APPLICABLE"),
    ("PENDING", "PENDING"),
    ("#REF!", "SPREADSHEET_ERROR"),
    ("abc", "UNPARSEABLE_AMOUNT"),
    ("12.3.4", "UNPARSEABLE_AMOUNT"),
])
def test_invalid_amounts_are_null_with_reason_never_zero(raw, reason):
    value, got_reason = parse_amount(raw, "USD")
    assert value is None
    assert got_reason == reason


def test_result_has_exactly_two_decimal_places():
    value, _ = parse_amount("100", "USD")
    assert str(value) == "100.00"
    value, _ = parse_amount("99.999", "USD")  # round half up
    assert str(value) == "100.00"
