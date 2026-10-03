"""Date and timestamp parser tests — real formats from the data pack."""

from datetime import date, datetime, timezone

import pytest

from pipeline.parsers.dates import (
    check_not_after,
    derive_period,
    parse_date,
    parse_timestamp_utc,
)


class TestParseDate:
    def test_same_numeric_date_differs_by_system_order(self):
        # The brief's example: 05/03/2024 is 5 March for day-first MEDITECH.
        assert parse_date("05/03/2024", "DMY")[0] == date(2024, 3, 5)
        assert parse_date("05/03/2024", "MDY")[0] == date(2024, 5, 3)

    @pytest.mark.parametrize("raw,order,expected", [
        ("2023-04-08", "MDY", date(2023, 4, 8)),
        ("04/18/2024", "MDY", date(2024, 4, 18)),
        ("2/11/2023", "MDY", date(2023, 2, 11)),
        ("13-06-1992", "DMY", date(1992, 6, 13)),
        ("19-08-2024", "DMY", date(2024, 8, 19)),
        ("11-11-24", "MDY", date(2024, 11, 11)),       # two-digit year -> 20xx
        ("May 14, 2023", "MDY", date(2023, 5, 14)),
        ("May 5, 2023", "MDY", date(2023, 5, 5)),
        ("December 16, 2023", "MDY", date(2023, 12, 16)),
        ("21 Feb 2023", "MDY", date(2023, 2, 21)),
        ("6 Nov 2024", "MDY", date(2024, 11, 6)),
        ("January 25, 1986", "MDY", date(1986, 1, 25)),
        ("2023-08-14T00:00:00", "MDY", date(2023, 8, 14)),  # ISO datetime in a date field
    ])
    def test_supported_formats(self, raw, order, expected):
        value, reason = parse_date(raw, order)
        assert reason is None
        assert value == expected

    @pytest.mark.parametrize("raw,reason", [
        ("", "MISSING"),
        (None, "MISSING"),
        ("TBD", "NOT_PROVIDED"),
        ("2024-02-30", "INVALID_DATE"),     # planted impossible date
        ("31/31/2024", "INVALID_DATE"),
        ("not a date", "UNPARSEABLE_DATE"),
    ])
    def test_invalid_dates(self, raw, reason):
        value, got = parse_date(raw, "MDY")
        assert value is None
        assert got == reason

    def test_future_date_flagged_against_delivery_date(self):
        value, reason = parse_date("2031-06-22", "MDY")
        assert reason is None  # parseable...
        assert check_not_after(value, date(2025, 1, 6)) == "FUTURE_DATE"  # ...but post-delivery
        assert check_not_after(date(2024, 12, 31), date(2025, 1, 6)) is None


class TestParseTimestampUtc:
    def test_epic_iso_zulu(self):
        value, reason = parse_timestamp_utc("2023-06-22T09:02:32Z", "UTC", "MDY")
        assert reason is None
        assert value == datetime(2023, 6, 22, 9, 2, 32, tzinfo=timezone.utc)

    def test_athena_naive_space_is_utc(self):
        value, _ = parse_timestamp_utc("2023-04-28 18:57:59", "UTC", "MDY")
        assert value == datetime(2023, 4, 28, 18, 57, 59, tzinfo=timezone.utc)

    def test_athena_v2_explicit_offset_wins(self):
        value, _ = parse_timestamp_utc("2024-11-21T18:14:31-06:00", "UTC", "MDY")
        assert value == datetime(2024, 11, 22, 0, 14, 31, tzinfo=timezone.utc)

    def test_meditech_wall_clock_chicago_cdt(self):
        # 26 Oct 2024 is Central Daylight Time (UTC-5)
        value, _ = parse_timestamp_utc("26/10/2024 07:32:41", "America/Chicago", "DMY")
        assert value == datetime(2024, 10, 26, 12, 32, 41, tzinfo=timezone.utc)

    def test_meditech_wall_clock_chicago_cst(self):
        # 15 Jan 2024 is Central Standard Time (UTC-6)
        value, _ = parse_timestamp_utc("15/01/2024 08:00:00", "America/Chicago", "DMY")
        assert value == datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc)

    def test_dst_fallback_ambiguity_resolves_to_standard_time(self):
        # 01:30 on 3 Nov 2024 occurs twice in Chicago; assumption A11: fold=1
        # takes the second (standard-time, UTC-6) occurrence -> 07:30 UTC.
        value, _ = parse_timestamp_utc("03/11/2024 01:30:00", "America/Chicago", "DMY")
        assert value == datetime(2024, 11, 3, 7, 30, 0, tzinfo=timezone.utc)

    def test_missing_and_garbage(self):
        assert parse_timestamp_utc("", "UTC", "MDY") == (None, "MISSING")
        assert parse_timestamp_utc("soon", "UTC", "MDY") == (None, "UNPARSEABLE_TIMESTAMP")

    def test_equal_instants_across_representations(self):
        # The batch_003 replay of encounter 139507: same instant, two spellings.
        a, _ = parse_timestamp_utc("2024-10-29 19:52:47", "UTC", "MDY")
        b, _ = parse_timestamp_utc("2024-10-29T14:52:47-05:00", "UTC", "MDY")
        assert a == b


def test_derive_period():
    assert derive_period(date(2024, 1, 15)) == (2024, 1, 1)
    assert derive_period(date(2024, 12, 31)) == (2024, 4, 12)
    assert derive_period(date(2023, 7, 1)) == (2023, 3, 7)
