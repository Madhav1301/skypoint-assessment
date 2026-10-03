# Data quality report — batch_001

- **Status:** ACCEPTED
- **Delivered at:** 2025-01-06T06:00:00Z

## Row reconciliation

`received = new versions + duplicates + stale + quarantined`

| File | Received | New versions | Duplicates | Stale | Quarantined |
|---|---:|---:|---:|---:|---:|
| encounters_athena_clinics.csv (ACCEPTED) | 1090 | 1062 | 8 | 0 | 20 |
| encounters_epic_north.csv (ACCEPTED) | 1432 | 1408 | 17 | 0 | 7 |
| encounters_legacy_meditech.csv (ACCEPTED) | 341 | 336 | 5 | 0 | 0 |
| **Total** | **2863** | **2806** | **30** | **0** | **27** |

## Checks

| Severity | Check | Rows flagged |
|---|---|---:|
| error | UNRESOLVED_FACILITY | 27 |
| warning | DISCHARGE_BEFORE_ADMIT | 7 |
| warning | INVALID_ADMIT_DATE | 17 |
| warning | INVALID_BILLED_AMOUNT | 50 |
| warning | INVALID_DX_CODE | 29 |
| warning | INVALID_NPI | 18 |
| warning | MISSING_DOB_NO_LINKAGE | 28 |
| warning | UNMAPPED_ENCOUNTER_TYPE | 21 |
| warning | UNMAPPED_PAYER | 19 |
