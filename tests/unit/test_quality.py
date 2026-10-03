"""Publish-gate math and DQ check-id mapping."""

from pipeline.quality import check_id_for_warning, gate_passes


def test_gate_boundary_inclusive():
    assert gate_passes(100, 10, 0.10) == (True, 0.10)    # exactly at threshold passes
    assert gate_passes(100, 11, 0.10)[0] is False
    assert gate_passes(100, 0, 0.10) == (True, 0.0)
    assert gate_passes(0, 0, 0.10) == (True, 0.0)        # empty batch is trivially fine


def test_check_id_mapping():
    assert check_id_for_warning("admit_date:FUTURE_DATE") == "INVALID_ADMIT_DATE"
    assert check_id_for_warning("discharge_date:DISCHARGE_BEFORE_ADMIT") == "DISCHARGE_BEFORE_ADMIT"
    assert check_id_for_warning("discharge_date:INVALID_DATE") == "INVALID_DISCHARGE_DATE"
    assert check_id_for_warning("billed_amount:PENDING") == "INVALID_BILLED_AMOUNT"
    assert check_id_for_warning("attending_npi:INVALID_NPI_LUHN") == "INVALID_NPI"
    assert check_id_for_warning("primary_dx_code:ICD9_LEGACY") == "INVALID_DX_CODE"
    assert check_id_for_warning("claim_status:MISSING") == "UNMAPPED_CLAIM_STATUS"
    assert check_id_for_warning("patient:MISSING_DOB_NO_LINKAGE") == "MISSING_DOB_NO_LINKAGE"
