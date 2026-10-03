"""Row transformation: raw canonical strings -> one cleaned, de-identified row.

This is where Task 2 (clean and standardise) and Task 3 (protect patient
data) meet the error/warning taxonomy of config/dq_rules.yaml:

  errors   -> the row is quarantined (identity, time-ordering or facility
              grain is broken): MISSING_SOURCE_RECORD_ID, unusable
              last_updated_ts, unresolved facility
  warnings -> the row stands; the degraded field is null with a reason

PHI boundary: the returned CleanedRow carries no name, MRN, phone, full DOB
or full ZIP — only age_band, zip3, standardised sex and the HMAC patient_key.
Raw values are kept beside cleaned values for NON-PHI fields only; PHI
originals remain reachable solely through the raw layer via lineage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from .parsers import amounts, categorical, dates, dx, facility, npi as npi_parser
from . import phi

ERROR_MISSING_RECORD_ID = "MISSING_SOURCE_RECORD_ID"
ERROR_INVALID_TS = "INVALID_LAST_UPDATED_TS"
ERROR_UNRESOLVED_FACILITY = "UNRESOLVED_FACILITY"


@dataclass(frozen=True)
class CleanContext:
    """Everything clean_row needs, assembled once per run (lookups) and
    refreshed per batch (delivered date for future-date checks)."""

    conventions: dict            # source_system -> {date_order, amount_unit, timestamp_timezone}
    alias_lookup: dict
    encounter_type_lookup: dict
    claim_status_lookup: dict
    payer_lookup: dict
    sex_lookup: dict
    icd10_codes: set
    patient_key_secret: str
    delivered_date: date


def build_context(
    conventions: dict,
    facility_aliases_cfg: dict,
    value_mappings_cfg: dict,
    icd10_codes: set,
    patient_key_secret: str,
    delivered_date: date,
) -> CleanContext:
    return CleanContext(
        conventions=conventions,
        alias_lookup=facility.build_alias_lookup(facility_aliases_cfg),
        encounter_type_lookup=categorical.build_lookup(value_mappings_cfg["encounter_type"]),
        claim_status_lookup=categorical.build_lookup(value_mappings_cfg["claim_status"]),
        payer_lookup=categorical.build_lookup(value_mappings_cfg["payer_category"]),
        sex_lookup=categorical.build_lookup(value_mappings_cfg["patient_sex"]),
        icd10_codes=icd10_codes,
        patient_key_secret=patient_key_secret,
        delivered_date=delivered_date,
    )


@dataclass
class CleanedRow:
    source_system: str
    source_record_id: str | None

    facility_id: str | None = None
    facility_name_raw: str | None = None

    patient_key: str | None = None
    linkage_scope: str | None = None          # PERSON (linkable) | IDENTITY (no DOB)
    age_band: str = "UNKNOWN"
    sex: str = "UNKNOWN"
    zip3: str | None = None

    admit_date: date | None = None
    admit_date_raw: str | None = None
    discharge_date: date | None = None
    discharge_date_raw: str | None = None
    discharge_before_admit: bool = False
    length_of_stay_days: int | None = None
    admit_year: int | None = None
    admit_quarter: int | None = None
    admit_month: int | None = None

    encounter_type: str = "UNKNOWN"
    encounter_type_raw: str | None = None
    claim_status: str | None = None
    claim_status_raw: str | None = None
    payer_category: str = "UNKNOWN"
    payer_name_raw: str | None = None
    billed_amount: Decimal | None = None
    billed_amount_raw: str | None = None

    attending_npi: str | None = None
    attending_npi_raw: str | None = None
    attending_provider_name_raw: str | None = None

    primary_dx_code: str | None = None
    primary_dx_raw: str | None = None
    dx_outcome: str = "MISSING"

    last_updated_ts_utc: datetime | None = None
    last_updated_ts_raw: str | None = None

    reasons: dict = field(default_factory=dict)   # field -> reason code (nulls and flags)
    errors: list = field(default_factory=list)    # quarantine-level reason codes
    warnings: list = field(default_factory=list)  # kept-but-flagged reason codes

    @property
    def is_quarantined(self) -> bool:
        return bool(self.errors)


def _blank_to_none(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value


def clean_row(raw: dict, ctx: CleanContext) -> CleanedRow:
    """Transform one raw canonical row (all strings) into a CleanedRow."""
    system = raw["source_system"]
    conv = ctx.conventions[system]
    date_order = conv["date_order"]

    row = CleanedRow(
        source_system=system,
        source_record_id=_blank_to_none(raw.get("source_record_id")),
        facility_name_raw=raw.get("facility_name"),
        admit_date_raw=raw.get("admit_date"),
        discharge_date_raw=raw.get("discharge_date"),
        encounter_type_raw=raw.get("encounter_type"),
        claim_status_raw=raw.get("claim_status"),
        payer_name_raw=raw.get("payer_name"),
        billed_amount_raw=raw.get("billed_amount"),
        attending_npi_raw=raw.get("attending_npi"),
        attending_provider_name_raw=raw.get("attending_provider_name"),
        primary_dx_raw=raw.get("primary_dx_code"),
        last_updated_ts_raw=raw.get("last_updated_ts"),
    )

    def note(field_name: str, reason: str | None) -> None:
        """Record a warning-level reason; error-level reasons are appended
        to row.errors inline where they occur."""
        if reason is None:
            return
        row.reasons[field_name] = reason
        row.warnings.append(f"{field_name}:{reason}")

    # --- identity (error level) ---
    if row.source_record_id is None:
        row.errors.append(ERROR_MISSING_RECORD_ID)
        row.reasons["source_record_id"] = ERROR_MISSING_RECORD_ID

    # --- version ordering (error level) ---
    ts, ts_reason = dates.parse_timestamp_utc(
        raw.get("last_updated_ts"), conv["timestamp_timezone"], date_order
    )
    row.last_updated_ts_utc = ts
    if ts_reason:
        row.reasons["last_updated_ts"] = ts_reason
        row.errors.append(ERROR_INVALID_TS)

    # --- facility (error level) ---
    facility_id, fac_reason = facility.resolve_facility(
        raw.get("facility_name"), system, ctx.alias_lookup
    )
    row.facility_id = facility_id
    if fac_reason:
        row.reasons["facility_name"] = fac_reason
        row.errors.append(ERROR_UNRESOLVED_FACILITY)

    # --- dates (warning level) ---
    admit, admit_reason = dates.parse_date(raw.get("admit_date"), date_order)
    if admit is not None and admit_reason is None:
        future = dates.check_not_after(admit, ctx.delivered_date)
        if future:
            admit, admit_reason = None, future
    row.admit_date = admit
    note("admit_date", admit_reason)

    discharge, discharge_reason = dates.parse_date(raw.get("discharge_date"), date_order)
    if discharge is not None and discharge_reason is None:
        future = dates.check_not_after(discharge, ctx.delivered_date)
        if future:
            discharge, discharge_reason = None, future
    row.discharge_date = discharge
    if discharge_reason == "MISSING":
        # An absent discharge is the normal state for ambulatory encounters:
        # the reason code is recorded, but it is not a data-quality warning.
        row.reasons["discharge_date"] = discharge_reason
    else:
        note("discharge_date", discharge_reason)

    if admit is not None and discharge is not None:
        if discharge < admit:
            row.discharge_before_admit = True
            row.warnings.append("discharge_date:DISCHARGE_BEFORE_ADMIT")
        else:
            row.length_of_stay_days = (discharge - admit).days
    if admit is not None:
        row.admit_year, row.admit_quarter, row.admit_month = dates.derive_period(admit)

    # --- categoricals (warning level) ---
    row.encounter_type, et_reason = categorical.map_encounter_type(
        raw.get("encounter_type"), ctx.encounter_type_lookup
    )
    note("encounter_type", et_reason)

    row.claim_status, cs_reason = categorical.map_claim_status(
        raw.get("claim_status"), ctx.claim_status_lookup
    )
    note("claim_status", cs_reason)

    row.payer_category, payer_reason = categorical.map_payer_category(
        raw.get("payer_name"), ctx.payer_lookup
    )
    note("payer_name", payer_reason)

    # --- money (warning level) ---
    row.billed_amount, amount_reason = amounts.parse_amount(
        raw.get("billed_amount"), conv["amount_unit"]
    )
    note("billed_amount", amount_reason)

    # --- NPI (warning level; roster membership checked at the provider join) ---
    row.attending_npi, npi_reason = npi_parser.clean_npi(raw.get("attending_npi"))
    note("attending_npi", npi_reason)

    # --- diagnosis (warning level) ---
    row.primary_dx_code, row.dx_outcome = dx.normalize_dx(
        raw.get("primary_dx_code"), ctx.icd10_codes
    )
    if row.dx_outcome not in ("IN_REFERENCE",):
        row.warnings.append(f"primary_dx_code:{row.dx_outcome}")

    # --- PHI transforms: nothing identifying leaves this function ---
    row.sex, sex_reason = categorical.map_sex(raw.get("patient_sex"), ctx.sex_lookup)
    note("patient_sex", sex_reason)

    dob, dob_reason = dates.parse_date(raw.get("patient_dob"), date_order)
    if dob is not None and dates.check_not_after(dob, ctx.delivered_date):
        dob, dob_reason = None, "FUTURE_DATE"
    if dob_reason and dob_reason != "MISSING":
        note("patient_dob", dob_reason)
    row.age_band = phi.age_band_at(dob, row.admit_date)

    row.zip3, zip_reason = phi.zip3(raw.get("patient_zip"))
    note("patient_zip", zip_reason)

    mk = phi.match_key(
        raw.get("patient_last_name"), raw.get("patient_first_name"), dob, row.sex
    )
    row.patient_key = phi.patient_key(
        ctx.patient_key_secret, match=mk,
        source_system=system, mrn=raw.get("patient_mrn"),
    )
    if row.patient_key is None:
        row.linkage_scope = None
        row.warnings.append("patient:NO_IDENTITY")
    else:
        row.linkage_scope = "PERSON" if mk else "IDENTITY"
        if mk is None:
            row.warnings.append("patient:MISSING_DOB_NO_LINKAGE")

    return row
