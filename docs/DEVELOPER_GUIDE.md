# Developer guide — the solution, end to end

This guide explains every component of the pipeline in plain language:
what lives where, what each function does, and why it exists. A new
developer should be able to navigate the codebase with it; a non-technical
reader should be able to follow the story. Jargon is explained in the
[glossary](#9-glossary) at the end.

---

## The 60-second version

Three hospital record systems each drop a weekly CSV of patient visits into
a folder, with a packing slip (manifest) stating how many rows each file has
and a tamper-proof fingerprint for each file. This pipeline, run by one
command, does five things per weekly batch:

1. **Checks the packing slip** — a damaged delivery is rejected whole,
   recorded, and the run carries on.
2. **Keeps an untouched copy** of every row, stamped with exactly where it
   came from (batch, file, row number).
3. **Cleans every field** using each system's documented conventions —
   never guessing: unfixable values become empty *with a reason code*, and
   rows that can't be trusted go to a quarantine table.
4. **Protects patients** — names, record numbers, birth dates and phone
   numbers never leave the first copy; downstream, each patient is a coded
   key.
5. **Keeps history straight** — visits are re-sent with corrections week
   after week; the pipeline keeps every version, knows which is current,
   and ignores duplicates and out-of-date replays.

The result is a small warehouse (facts + dimensions), one required report
(`chronic_acute_encounters.csv`), and audit tables proving that every row
landed somewhere: `received = new versions + duplicates + stale + quarantined`.

---

## 1. Repository map

```
skypoint-assessment/
├── config/                      Rules that change without code changes
│   ├── schema_contracts.yaml      allowed file layouts per source system
│   ├── value_mappings.yaml        spelling variants -> canonical values
│   ├── facility_aliases.yaml      facility name variants -> facility IDs
│   └── dq_rules.yaml              every quality check + its severity
├── data/candidate_pack/         The input data (mounted read-only)
├── docs/DEVELOPER_GUIDE.md      This document
├── output/                      Everything the pipeline produces
├── scripts/query.py             Helper to run the README's example queries
├── src/pipeline/                The program itself (detailed in §3)
├── tests/                       263 automated checks (unit + integration)
├── .github/workflows/verify.yml Fresh-machine verification on every push
├── Dockerfile, docker-compose.yml, requirements.txt, .env.example
└── README.md, ARCHITECTURE.md, AI_USAGE.md
```

Guiding principle: **code is for logic that rarely changes; config is for
facts that change often** (a new facility spelling, a new payer name, a
schema change). Most "new requirements" are a one-line YAML edit.

---

## 2. The journey of one batch (how a run works)

`docker compose up --build` starts `python -m pipeline.run`, which processes
each batch folder in order. For one batch, the steps are:

| # | Step | Where | What happens |
|---|---|---|---|
| 1 | Skip check | `ingest.process_batch` | Already processed? Do nothing (re-runs are free). |
| 2 | Manifest gate | `manifest.py` | Recount rows, recompute SHA-256 fingerprints. Any mismatch → whole batch rejected, audited, run continues. |
| 3 | Schema contract | `schema_contract.py` | File header must exactly match a configured layout. Known new layouts map via config; unknown ones stop the batch. |
| 4 | Read + clean | `ingest.py` → `cleaning.py` → `parsers/` | Every row parsed field by field in memory. Errors → quarantine list; warnings → kept + flagged. |
| 5 | Version classification | `versioning.py` | Each row is NEW, DUPLICATE or STALE based on (encounter id + timestamp in UTC). |
| 6 | Publish gate | `quality.py` | If more than 10% of rows failed error-level checks, the batch publishes nothing. |
| 7 | One write transaction | `ingest.py` + `bulk.py` | Raw rows, new versions, quarantine rows, audit counts all land together — or not at all. |
| 8 | Model + outputs | `model.py` | Reference tables and gold views rebuilt; every table exported as CSV; per-batch DQ report written. |

The key safety idea: **nothing-until-decided**. Steps 2–6 touch no tables.
Only when a batch fully qualifies does step 7 write — so a failed batch can
never leave half-loaded data behind.

---

## 3. Module-by-module reference (`src/pipeline/`)

### run.py — the front door
*One command in, everything else orchestrated.*

| Function | What it does |
|---|---|
| `main()` | Reads settings, refuses to run without a patient-key secret, processes all pending batches in order, rebuilds the model, writes outputs. Returns exit code 0 even when a batch was rejected (that's reported, not fatal). |
| `discover_batches(data_dir)` | Lists `landing/batch_*` folders in order. |
| `build_run_config(settings)` | Loads all YAML configs + reference metadata once into a single `RunConfig` object that every batch uses. |

### config.py — settings and reference loading

| Function | What it does |
|---|---|
| `Settings` / `settings_from_env()` | All runtime options come from environment variables (paths, secret, gate threshold, log level) — nothing is hardcoded. |
| `load_yaml_config(dir, name)` | Reads one YAML config file. |
| `load_source_conventions(data_dir)` | Reads the reference JSON that documents each system's date order, amount unit and timezone — the pipeline *reads* these rules rather than re-encoding them. |
| `load_icd10_reference(data_dir)` | Reads the ICD-10 diagnosis reference into a dictionary (code → description, category, chronic flag). |

### logging_setup.py — structured, PHI-safe logs

| Function | What it does |
|---|---|
| `JsonFormatter` | Renders every log line as JSON with timestamp, level, message and context fields — machine-readable operations logs. |
| `setup_logging(level)` | Installs the formatter on stdout. |
| `info/warning/error/debug(msg, **ctx)` | Convenience wrappers. Rule enforced by convention and tests: context may contain batch ids, file names, row numbers and counts — **never field values**, so PHI cannot leak through logs. |

### db.py — the database schema, in one place

| Item | What it does |
|---|---|
| `RAW_COLUMNS`, `CLEAN_COLUMNS`, `QUARANTINE_COLUMNS` | Ordered column-name → type maps. Single source of truth: the DDL, the bulk loader and the row serialisers all derive from these, so they can never drift apart. |
| `DDL` | `CREATE TABLE/SCHEMA IF NOT EXISTS` statements for all five schemas (raw, clean, ref, gold, meta). |
| `connect(db_path)` / `init_schema(con)` | Opens the DuckDB file and ensures the schema exists. |

### manifest.py — gate 1

| Function | What it does |
|---|---|
| `load_manifest(batch_dir)` | Parses `manifest.json`; missing or malformed → `ManifestError`. |
| `sha256_of_file(path)` | Streams a file through SHA-256 — the tamper fingerprint. |
| `count_csv_data_rows(path)` | Counts CSV *records* (not text lines) minus the header, using the csv module so a quoted value containing a line break can't miscount. |
| `validate_batch_files(dir, manifest)` | Compares every file's actual rows + fingerprint against the manifest; returns per-file pass/fail with reasons (`ROW_COUNT_MISMATCH`, `SHA256_MISMATCH`, `FILE_MISSING`). |

### schema_contract.py — gate for file layouts

| Function | What it does |
|---|---|
| `match_contract(contracts, system, header)` | The file's header must match a configured layout *exactly* (names and order). On match, returns the mapping to canonical column names (e.g. ATHENA v2's `total_charge` → `billed_amount`) plus "extra" columns preserved separately. On no match, raises an error that names the closest version and exactly which columns are missing/unexpected — an actionable message, because unknown schema changes are supposed to stop the batch. |

### parsers/ — one small, pure function per field family

Every parser returns `(value, reason)`: exactly one is set. A null value
always carries a reason code; raw input is never thrown away (it's stored
beside the cleaned value). Pure functions = trivially unit-testable — 230+
of the tests point here.

**amounts.py**

| Function | What it does |
|---|---|
| `parse_amount(raw, unit)` | Turns `"$1,089.88"`, `"USD 348.11"`, `"$2.07K"` or MEDITECH's integer cents (`1845000` → `18450.00`) into an exact decimal with 2 places. Blank/`N/A`/`PENDING` → null + reason — **never zero**, because a fake zero would silently corrupt revenue totals. |

**dates.py**

| Function | What it does |
|---|---|
| `parse_date(raw, date_order)` | Handles ISO, month names ("January 25, 1986"), two-digit years, and ambiguous numerics read with the *system's* day order — `05/03/2024` is 3 May for a day-first system and 5 March… the other way round. Impossible dates (Feb 30) → null + reason. |
| `check_not_after(date, limit)` | Flags dates after the batch's delivery date (catches the planted year-2031 rows). |
| `parse_timestamp_utc(raw, tz, date_order)` | Converts `last_updated_ts` to UTC. An explicit offset in the value wins; otherwise the system's documented timezone applies (MEDITECH = Chicago wall-clock). This is the foundation of correct version ordering. |
| `derive_period(date)` | (year, quarter, month) for reporting. |

**npi.py**

| Function | What it does |
|---|---|
| `clean_npi(raw)` | Strips harmless formatting (Excel's `1608565176.0` artefact), then validates: 10 digits whose last digit passes the Luhn check over `80840` + first 9 — the official CMS rule. Distinguishes `INVALID_NPI_FORMAT` from `INVALID_NPI_LUHN`. |
| `luhn_80840_valid(npi)` | The check-digit math itself. |

**dx.py**

| Function | What it does |
|---|---|
| `normalize_dx(raw, reference)` | Uppercases, strips trailing descriptions ("I50.22 - Chronic…"), inserts the missing dot (`E1165` → `E11.65`), then classifies into exactly four outcomes: in reference / valid-but-unknown / legacy ICD-9 (never translated) / unparseable. Judgment call documented in the docstring: only purely numeric codes count as ICD-9. |

**categorical.py**

| Function | What it does |
|---|---|
| `build_lookup(mapping)` | Inverts the YAML (canonical → variants) into a fast lookup. |
| `map_encounter_type / map_payer_category / map_sex` | Variant → canonical; unmapped → `UNKNOWN` + flag. |
| `map_claim_status` | Same, but with **no** UNKNOWN bucket: an unmapped status stays null, so the export's "not VOID" filter fails closed. |

**facility.py**

| Function | What it does |
|---|---|
| `normalize_facility_name(raw)` | Uppercase, drop punctuation, collapse spaces — so `"St. Brendan's"` and `"ST BRENDANS"` compare equal. |
| `resolve_facility(raw, system, lookup)` | Exact lookup in the curated alias table, per system. Unknown names (e.g. "Westfield Surgical Center") → `UNRESOLVED_FACILITY` → quarantine. Deliberately no fuzzy matching: deterministic and auditable beats probabilistic for 8 facilities. |

### phi.py — patient privacy primitives

| Function | What it does |
|---|---|
| `age_band_at(dob, admit)` | Date of birth → `0-17 / 18-39 / 40-64 / 65+` at admission. The analyst gets the band; the birth date goes no further. |
| `zip3(raw)` | 5-digit ZIP → first three digits. |
| `normalize_name_part / first_given_name` | Name normalisation for matching: case, punctuation and middle initials removed. |
| `match_key(last, first, dob, sex)` | The brief's minimum linkage rule as a string key; `None` when DOB is missing (then no cross-system linking, by rule). |
| `patient_key(secret, …)` | HMAC-SHA256 of either `person:<match key>` or `identity:<system>:<mrn>` — two namespaces that can't collide. Same person at two hospitals → same key, with no lookup table. Without the secret (an environment variable) it refuses to run. |

### cleaning.py — one row in, one safe row out

| Item | What it does |
|---|---|
| `CleanContext` / `build_context(...)` | Bundles everything cleaning needs: per-system conventions, all lookups, the ICD reference, the secret, and the batch's delivery date. |
| `CleanedRow` | The output record: every cleaned field, raw originals for non-PHI fields, a `reasons` map (field → code), `warnings` (kept-but-flagged) and `errors` (quarantine-level). **By construction it has no fields for name, MRN, DOB, phone, ZIP or chief complaint** — PHI can't survive because there is nowhere to put it. A test iterates every field to prove it. |
| `clean_row(raw, ctx)` | Runs all parsers in order, applies the error/warning taxonomy from `dq_rules.yaml` (errors: no record id, unusable timestamp, unresolved facility), derives length-of-stay and periods, and produces the patient key. |

### versioning.py — duplicates, stale replays, current state

| Item | What it does |
|---|---|
| `VersionState` | In-memory index of all known versions: a set of (system, record id, timestamp) plus the newest timestamp per encounter. Loaded from the database once per run. |
| `classify_version(state, …)` | NEW, DUPLICATE (exact instant already held — catches byte-copies *and* reformatted replays) or STALE (older than the newest held → can never overwrite current state). Rows are processed in file order, so results are deterministic. |

### quality.py — the publish gate and DQ roll-up

| Function | What it does |
|---|---|
| `gate_passes(received, quarantined, threshold)` | Error-share ≤ threshold (10% default)? Duplicates/stale don't count — they're normal feed behaviour, not quality failures. |
| `check_id_for_warning(tag)` | Maps field-level warning tags to the check ids documented in `dq_rules.yaml`. |
| `aggregate_dq(rows)` | Counts rows per (check, severity) for the batch's DQ report. |

### bulk.py — fast loading

| Function | What it does |
|---|---|
| `bulk_insert(con, table, columns, rows)` | Writes rows to a temp CSV and loads them with one vectorised `read_csv` insert. Replaced per-row inserts that took ~15 ms each — the full pipeline went from ~55 s to ~3 s. Nulls are encoded as `\N` so genuine empty strings survive verbatim in the raw layer. |

### ingest.py — the batch conductor

| Item | What it does |
|---|---|
| `RunConfig` | Everything a batch needs (contracts, conventions, lookups, secret, threshold), built once per run. |
| `read_encounter_file(path, system, contracts)` | Opens with `utf-8-sig` (eats BOMs) and the csv module (handles CRLF/LF and quoted fields), matches the schema contract, and returns canonical-named records plus extras. |
| `_process_file(...)` | Per file: builds raw rows, cleans each record, routes quarantine rows, classifies versions, accumulates the counts. |
| `process_batch(con, batch_dir, cfg, state)` | The eight steps from §2: skip-check → manifest gate → read/clean → gate → single transaction → audit. Every exit path writes audit rows explaining itself. |
| `_audit / _register / _write_dq` | Small writers for the audit table, the processed-batch registry (what makes re-runs no-ops) and the DQ counts. |

### scd2.py — the provider dimension's time machine

| Item | What it does |
|---|---|
| `load_roster_snapshots(dir)` | Reads the three roster CSVs with their as-of dates. |
| `build_provider_scd2(snapshots)` | Turns snapshots into validity periods per provider: earliest snapshot back-dated (it "applies to all earlier dates"), unchanged snapshots collapsed into one period, attribute changes closing one period and opening the next, disappearances closing the last one. Handles the real cases in the data: employment flips, a facility move, and a surname change on the same NPI (King → Olson) — which is also why providers are matched by NPI, never by name. |

### model.py — the warehouse and the outputs

| Item | What it does |
|---|---|
| `build_reference(con, data_dir)` | Rebuilds `ref.facilities`, `ref.icd10` and `ref.provider_scd2` from the reference files — idempotent by construction. |
| `GOLD_VIEWS` / `build_gold(con)` | The dimensional model as SQL views over the insert-only clean layer: the versions fact, the current-state view (latest version per encounter via a window function), six dimensions, and the Task 7 export view with all six fail-closed filters and the 30-day readmission flag (searched across *all* encounters, any facility). |
| `write_outputs(con, dir)` | One CSV per table, each with a deterministic sort — which is what makes "re-run and compare bytes" a valid test. |
| `write_dq_reports(con, dir)` | One Markdown report per batch: status, the reconciliation table, checks by severity. Deliberately contains no wall-clock values so re-runs produce identical files. |

### scripts/query.py — demo helper

Runs any of the README's six example queries by number (`python
scripts/query.py 2`) or ad-hoc SQL via `--sql`, against the warehouse —
no quoting gymnastics on Windows, timestamps displayed in UTC.

---

## 4. The configuration files (when would you edit each?)

| File | You edit it when… | Example |
|---|---|---|
| `schema_contracts.yaml` | a source system announces a new file layout | ATHENA's v2 entry maps `total_charge` → `billed_amount` |
| `value_mappings.yaml` | a new spelling of a status/type/payer appears | add `"CLOSED - SETTLED"` under PAID |
| `facility_aliases.yaml` | a new facility name variant shows up in quarantine | add `"ST BRENDAN HOSP"` under FAC002 |
| `dq_rules.yaml` | you add a check or change a severity | documents *why* each check is error vs warning |
| `.env` (from `.env.example`) | secrets and knobs | `PATIENT_KEY_SECRET`, `PUBLISH_GATE_THRESHOLD` |

This table is the answer to most "add a new requirement" exercises: the
change is config, surfaced by quarantine, reviewed in a pull request — not
a code edit.

---

## 5. The database, table by table (what one row means)

| Table | One row is… |
|---|---|
| `raw.encounters` | one delivered file row, exactly as received (the only place patient identifiers exist) |
| `clean.encounter_versions` | one distinct accepted *version* of an encounter — never updated, never deleted |
| `gold.fact_encounter_current` | one encounter, as we currently believe it to be |
| `gold.dim_provider` | one provider during one validity period |
| `gold.dim_patient` | one pseudonymous patient key with non-identifying attributes |
| `gold.dim_facility / dim_diagnosis / dim_payer / dim_date` | one facility / diagnosis code / payer spelling / calendar day |
| `meta.quarantine` | one rejected row: where it came from and why (codes only) |
| `meta.batch_audit` | one batch or file outcome with the five reconciling counts |
| `meta.dq_results` | one (batch, check, severity) count |
| `meta.processed_batches` | one processed batch — the memory that makes re-runs no-ops |

---

## 6. Tests — which test proves which promise

```
tests/unit/          fast, no database needed (except tiny temp ones)
tests/integration/   run the real data pack end to end
```

| Promise | Proof |
|---|---|
| Every parser handles every observed mess | `test_amounts / dates / npi / dx / categorical / facility` (inputs lifted from the real data) |
| PHI cannot reach outputs | `test_cleaning::test_no_phi_survives…` + `test_full_pipeline::test_export_contains_no_phi_shapes` |
| Damaged batches reject whole, audited | `test_ingest_behavior::test_manifest_failure…` |
| Unknown schemas stop the batch | `…test_unknown_schema_rejects…` |
| Duplicates/stale never corrupt history | `test_versioning` + `test_ingest_behavior` + the 139507 case in `test_full_pipeline` |
| The gate blocks bad batches *entirely* | `…test_publish_gate_blocks_bad_batch_entirely` |
| Batch-by-batch == full rebuild, byte-identical | `test_full_pipeline::test_incremental_equals_full_rebuild` |
| As-of reporting is correct | `…test_as_of_batch_002_reporting` |
| SCD2 is point-in-time correct | `test_scd2` + `…test_scd2_point_in_time_join` |
| The README's SQL actually runs | `test_readme_queries` executes the six blocks verbatim |

Run them: `docker compose run --rm tests` (or `pytest -q` in the venv).

---

## 7. Docker & CI

- **Dockerfile** — Python 3.12 slim, pinned dependencies, code + config +
  tests copied in; default command runs the pipeline.
- **docker-compose.yml** — `pipeline` service (data mounted read-only at
  `/data`, outputs bind-mounted to `./output`, config via environment
  variables with safe defaults) and a `tests` service behind a profile.
- **.github/workflows/verify.yml** — on every push, a clean cloud machine
  clones the repo and runs the exact assessor flow: one command, exit 0,
  outputs asserted, batch_004 rejected-not-fatal, full suite in the
  container. Green run = the "works on a fresh machine" guarantee stays
  true.

---

## 8. Where would I change…? (live-demo cheat sheet)

| New requirement | Touch |
|---|---|
| New facility spelling | `config/facility_aliases.yaml` (it's already waiting in quarantine) |
| New payer / status / type variant | `config/value_mappings.yaml` |
| Source system changes its file layout | new version block in `config/schema_contracts.yaml` |
| New field-level validation | a parser in `src/pipeline/parsers/` + wiring in `cleaning.py` + a line in `dq_rules.yaml` + unit tests |
| Different gate threshold | `PUBLISH_GATE_THRESHOLD` env var |
| New output table | a view in `model.GOLD_VIEWS` + a line in `model._EXPORTS` |
| New source system | contract + aliases + conventions entry in the reference JSON; the code paths are already generic |

---

## 9. Glossary

| Term | Plain meaning |
|---|---|
| **Batch** | One weekly delivery: three CSVs + a manifest. |
| **Manifest** | The packing slip: expected row count and fingerprint per file. |
| **SHA-256** | A fingerprint of a file's exact bytes; change one character and it changes completely. |
| **PHI** | Protected Health Information — names, record numbers, birth dates, phones, ZIPs. |
| **HMAC** | A keyed hash: a scrambler that always scrambles the same input to the same code, but only for whoever holds the secret key. |
| **patient_key** | The pseudonym a patient gets downstream, built with HMAC. |
| **Version** | One snapshot of an encounter at a point in time; corrections create new versions. |
| **Stale row** | A version older than one we already hold — must never overwrite the newer truth. |
| **Quarantine** | The holding table for rows we refuse to model, each with reason codes and a pointer to its source row. |
| **Lineage** | The batch/file/row-number trail from any output number back to its source. |
| **Idempotent** | Running it twice changes nothing the second time. |
| **SCD2** | "Slowly changing dimension, type 2": keeping dated validity periods so you can ask what was true *on a given date*. |
| **UTC** | The universal clock; all timestamps are converted to it before comparing. |
| **Medallion (raw/clean/gold)** | The standard layering: as-delivered → cleaned → analysis-ready. |
| **Publish gate** | The circuit breaker: a batch with too many error rows publishes nothing. |
| **DuckDB** | A small analytics database that lives in one local file — "SQLite for analytics". |
