# Data quality report — batch_003

- **Status:** ACCEPTED
- **Delivered at:** 2025-01-20T06:00:00Z

## Row reconciliation

`received = new versions + duplicates + stale + quarantined`

| File | Received | New versions | Duplicates | Stale | Quarantined |
|---|---:|---:|---:|---:|---:|
| encounters_athena_clinics.csv (ACCEPTED) | 74 | 64 | 9 | 0 | 1 |
| encounters_epic_north.csv (ACCEPTED) | 83 | 71 | 12 | 0 | 0 |
| encounters_legacy_meditech.csv (ACCEPTED) | 31 | 27 | 4 | 0 | 0 |
| **Total** | **188** | **162** | **25** | **0** | **1** |

## Checks

| Severity | Check | Rows flagged |
|---|---|---:|
| error | UNRESOLVED_FACILITY | 1 |
| warning | INVALID_NPI | 1 |
| warning | MISSING_DOB_NO_LINKAGE | 2 |
