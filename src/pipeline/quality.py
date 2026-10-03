"""Publish gate and DQ aggregation (Task 6).

The gate counts only error-level rows (quarantined): duplicates and stale
replays are normal feed behaviour, not quality failures. Warning tags of the
form "field:REASON" roll up to the check ids documented in dq_rules.yaml.
"""

from __future__ import annotations

from collections import Counter

_WARNING_FIELD_TO_CHECK = {
    "admit_date": "INVALID_ADMIT_DATE",
    "discharge_date": "INVALID_DISCHARGE_DATE",
    "patient_dob": "INVALID_DOB",
    "patient_zip": "INVALID_ZIP",
    "patient_sex": "INVALID_SEX",
    "billed_amount": "INVALID_BILLED_AMOUNT",
    "attending_npi": "INVALID_NPI",
    "primary_dx_code": "INVALID_DX_CODE",
    "encounter_type": "UNMAPPED_ENCOUNTER_TYPE",
    "claim_status": "UNMAPPED_CLAIM_STATUS",
    "payer_name": "UNMAPPED_PAYER",
}
_WARNING_TAG_OVERRIDES = {
    "discharge_date:DISCHARGE_BEFORE_ADMIT": "DISCHARGE_BEFORE_ADMIT",
    "patient:MISSING_DOB_NO_LINKAGE": "MISSING_DOB_NO_LINKAGE",
    "patient:NO_IDENTITY": "NO_PATIENT_IDENTITY",
}


def gate_passes(rows_received: int, rows_quarantined: int, threshold: float) -> tuple[bool, float]:
    """(passed, error_share). An empty batch passes trivially."""
    if rows_received == 0:
        return True, 0.0
    share = rows_quarantined / rows_received
    return share <= threshold, share


def check_id_for_warning(tag: str) -> str:
    if tag in _WARNING_TAG_OVERRIDES:
        return _WARNING_TAG_OVERRIDES[tag]
    field = tag.split(":", 1)[0]
    return _WARNING_FIELD_TO_CHECK.get(field, tag)


def aggregate_dq(cleaned_rows) -> Counter:
    """Counter[(check_id, severity)] over a batch's cleaned rows."""
    counts: Counter = Counter()
    for row in cleaned_rows:
        for err in set(row.errors):
            counts[(err, "error")] += 1
        for tag in set(row.warnings):
            counts[(check_id_for_warning(tag), "warning")] += 1
    return counts
