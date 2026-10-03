# Data quality report — batch_002

- **Status:** ACCEPTED
- **Delivered at:** 2025-01-13T06:00:00Z

## Row reconciliation

`received = new versions + duplicates + stale + quarantined`

| File | Received | New versions | Duplicates | Stale | Quarantined |
|---|---:|---:|---:|---:|---:|
| encounters_athena_clinics.csv (ACCEPTED) | 147 | 129 | 14 | 0 | 4 |
| encounters_epic_north.csv (ACCEPTED) | 174 | 154 | 20 | 0 | 0 |
| encounters_legacy_meditech.csv (ACCEPTED) | 264 | 258 | 6 | 0 | 0 |
| **Total** | **585** | **541** | **40** | **0** | **4** |

## Checks

| Severity | Check | Rows flagged |
|---|---|---:|
| error | UNRESOLVED_FACILITY | 4 |
| warning | INVALID_ADMIT_DATE | 1 |
| warning | INVALID_BILLED_AMOUNT | 6 |
| warning | INVALID_DX_CODE | 17 |
| warning | INVALID_NPI | 1 |
| warning | MISSING_DOB_NO_LINKAGE | 4 |
| warning | UNMAPPED_ENCOUNTER_TYPE | 1 |
| warning | UNMAPPED_PAYER | 7 |
