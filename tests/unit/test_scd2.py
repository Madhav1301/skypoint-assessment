"""Provider SCD2 tests against the real roster snapshots."""

from datetime import date

import pytest

from pipeline.scd2 import BACKDATED_FROM, OPEN_ENDED_TO, build_provider_scd2, load_roster_snapshots


@pytest.fixture(scope="module")
def periods(data_dir):
    return build_provider_scd2(load_roster_snapshots(data_dir / "reference" / "provider_roster"))


def by_npi(periods, npi):
    return sorted((p for p in periods if p.npi == npi), key=lambda p: p.valid_from)


def test_unchanged_provider_collapses_to_one_backdated_open_period(periods):
    rows = by_npi(periods, "1406925663")  # Mueller: identical in all three snapshots
    assert len(rows) == 1
    assert rows[0].valid_from == BACKDATED_FROM
    assert rows[0].valid_to == OPEN_ENDED_TO
    assert rows[0].is_current


def test_surname_change_same_npi_king_to_olson(periods):
    rows = by_npi(periods, "1472671472")
    assert [(p.provider_last_name, p.employment_status, p.valid_from, p.valid_to) for p in rows] == [
        ("King", "Affiliated", BACKDATED_FROM, date(2024, 7, 1)),
        ("Olson", "Employed", date(2024, 7, 1), OPEN_ENDED_TO),
    ]


def test_employment_flip_point_in_time(periods):
    rows = by_npi(periods, "1228088641")  # Patel: Affiliated -> Employed at 2024-07-01
    assert len(rows) == 2
    march, august = date(2024, 3, 15), date(2024, 8, 1)
    at_march = [p for p in rows if p.valid_from <= march < p.valid_to]
    at_august = [p for p in rows if p.valid_from <= august < p.valid_to]
    assert at_march[0].employment_status == "Affiliated"
    assert at_august[0].employment_status == "Employed"


def test_dropped_provider_period_closes(periods):
    rows = by_npi(periods, "1615846452")  # O'Brien: gone from the 2024-07 roster
    assert len(rows) == 1
    assert rows[0].valid_to == date(2024, 7, 1)
    assert not rows[0].is_current

    rows = by_npi(periods, "1739241960")  # Thomas: gone from the 2025-01 roster
    assert rows[-1].valid_to == date(2025, 1, 1)


def test_mid_stream_joiner_is_not_backdated(periods):
    rows = by_npi(periods, "1472855687")  # Schmidt: first appears 2024-07-01
    assert rows[0].valid_from == date(2024, 7, 1)


def test_periods_never_overlap_and_cover_contiguously(periods):
    from collections import defaultdict

    grouped = defaultdict(list)
    for p in periods:
        grouped[p.npi].append(p)
    for npi, rows in grouped.items():
        rows.sort(key=lambda p: p.valid_from)
        for p in rows:
            assert p.valid_from < p.valid_to, npi
        for a, b in zip(rows, rows[1:]):
            assert a.valid_to <= b.valid_from, npi
