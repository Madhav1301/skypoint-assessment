"""Version classification unit tests: duplicate, stale, equal-instant rules."""

from datetime import datetime, timezone

from pipeline.versioning import VersionState, classify_version


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_new_then_duplicate_then_stale():
    state = VersionState()
    assert classify_version(state, "S", "R1", ts("2024-01-10T00:00:00")).outcome == "NEW"
    assert classify_version(state, "S", "R1", ts("2024-01-10T00:00:00")).outcome == "DUPLICATE"
    assert classify_version(state, "S", "R1", ts("2024-02-01T00:00:00")).outcome == "NEW"
    assert classify_version(state, "S", "R1", ts("2024-01-20T00:00:00")).outcome == "STALE"


def test_equal_instant_redelivery_is_duplicate_first_wins():
    # The 139507 scenario: the same instant arrives later in another format.
    state = VersionState()
    assert classify_version(state, "A", "139507", ts("2024-10-29T19:52:47")).outcome == "NEW"
    assert classify_version(state, "A", "139507", ts("2025-01-06T23:33:09")).outcome == "NEW"
    assert classify_version(state, "A", "139507", ts("2024-10-29T19:52:47")).outcome == "DUPLICATE"


def test_encounters_are_independent():
    state = VersionState()
    assert classify_version(state, "S", "R1", ts("2024-03-01T00:00:00")).outcome == "NEW"
    assert classify_version(state, "S", "R2", ts("2024-01-01T00:00:00")).outcome == "NEW"
    assert classify_version(state, "T", "R1", ts("2024-01-01T00:00:00")).outcome == "NEW"
