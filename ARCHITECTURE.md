# Architecture

This document covers design judgement and ownership: the data model as
built, how the same design runs in production on Azure Databricks (with the
Snowflake mapping noted), PHI governance, quality at scale, and cost.

---

## 1. Data model

### 1.1 Layers

| Layer (DuckDB schema) | Job | PHI? |
|---|---|---|
| `raw` | every accepted row verbatim, as text, + lineage (`batch_id`, `file_name`, `source_row_number`, `file_sha256`, `ingested_at`) | **yes — only here** |
| `clean` | one row per **distinct version** of an encounter: typed values, raw kept beside cleaned for non-PHI fields, null reason codes, de-identified patient attributes | no, by construction |
| `ref` | facility master, ICD-10 reference, provider SCD2 (derived from roster snapshots) | no |
| `gold` | views over `clean` + `ref`: facts, dimensions, the export | no |
| `meta` | batch_audit, quarantine, dq_results, processed_batches | no (lineage + codes only) |

### 1.2 Grain and keys

| Table | Grain (one row per…) | Key | Notes |
|---|---|---|---|
| `raw.encounters` | delivered file row | (batch_id, file_name, source_row_number) | immutable landing record |
| `clean.encounter_versions` | accepted distinct version | (source_system, source_record_id, **last_updated_ts_utc**) | insert-only; carries arrival batch |
| `gold.fact_encounter_current` | encounter | (source_system, source_record_id) | view: latest version, + version_count |
| `gold.dim_provider` | provider validity period | (npi, valid_from) | SCD2, valid_to exclusive |
| `gold.dim_patient` | person (or per-system identity) | patient_key | non-identifying attributes only |
| `gold.dim_facility` / `dim_diagnosis` / `dim_payer` / `dim_date` | entity | natural key | facility master / ICD-10 reference / observed payers / calendar |
| `meta.quarantine` | rejected row | lineage triple | reason codes; field→code map; no values |
| `meta.batch_audit` | batch × file (+ batch summary) | — | received = new + duplicates + stale + quarantined |

### 1.3 History, current state, and ordering

A version's identity is **(source_system, source_record_id, last_updated_ts
normalised to UTC)**. Timestamps are converted *before* comparison —
MEDITECH's are Chicago wall-clock, ATHENA's switch mid-stream to explicit
offsets — because 20 encounters in this pack change their current version if
you compare the strings naively.

- **Duplicates** (byte-identical or the same instant re-spelled) never create
  versions. The batch_003 replay of encounter 139507's old version is the
  canonical case: same instant, new schema spelling → duplicate.
- **Stale** rows (older than the newest held version) are counted and
  excluded, so they can never overwrite current state — even arriving weeks
  later.
- **Current state** is a window over the insert-only history: latest
  `last_updated_ts_utc` per encounter. Nothing is ever updated in place.

### 1.4 As-of reporting

Every version carries `source_batch_id`. "What did we believe at the end of
batch_002?" is a filter (`source_batch_id <= 'batch_002'`) plus the same
latest-per-encounter window — no snapshots to maintain, proven equal to an
actual run stopped at batch_002 by an integration test. Late-arriving
encounters update historical admit months correctly for the same reason: the
model's time axes (admit date vs arrival batch) are independent columns.

### 1.5 Provider SCD2

Built from the three roster snapshots with the brief's validity semantics:
earliest snapshot back-dated to cover all earlier dates; a snapshot's values
hold until the next; consecutive identical snapshots collapse into one
period; disappearing from a roster closes the period. Encounters join
point-in-time on `admit_date >= valid_from AND admit_date < valid_to`. The
data exercises every case: employment flips (Patel), facility moves, a
surname change on a stable NPI (King→Olson — which is also why provider
matching is by NPI, never by name), joiners (not back-dated) and leavers.

### 1.6 Patient identity

`patient_key` = HMAC-SHA256(secret, canonical input), truncated to 128 bits,
with two domain-separated input namespaces:

- `person:last|first|dob|sex` (normalised) — the brief's minimum rule. The
  same person at EPIC and ATHENA hashes to the same key with no match table.
- `identity:system:mrn` — when DOB is missing; never linked across systems.

**Risk trade-off:** the rule can falsely **merge** two real people sharing
name+DOB+sex (clinically the worse error: two patients' histories blend and
the readmission flag can fire across them) and falsely **split** one person
whose name or DOB is recorded differently (worse for utilisation metrics,
safer clinically). Production mitigation: probabilistic matching (e.g.
Fellegi–Sunter or Splink) with human review queues for borderline scores,
and persisted match decisions so keys stay stable.

---

## 2. Production design — Azure Databricks (Snowflake notes inline)

The local design maps one-to-one; nothing conceptual changes.

**Ingestion.** Source systems drop files + manifest into ADLS Gen2
(`landing/<system>/<batch>/`). An event-triggered job (file-notification
Auto Loader on the manifest, not the CSVs) runs the **manifest gate first**
— counts and SHA-256 computed on the storage copy — then loads accepted
files to a bronze Delta table with the same lineage columns. Batch
all-or-nothing stays: bronze/silver writes happen in one job run keyed by an
idempotent batch registry (Delta txn per table + registry check = effective
exactly-once; Snowflake: Snowpipe → stream + task, manifest check in the
task).

**Layer design.** Bronze/silver/gold Delta tables in Unity Catalog mirror
`raw`/`clean`/`gold` exactly, including reason-code columns and the
insert-only version history. The current-state view stays a window function
(or a materialised gold table refreshed per batch).

**Ordered upserts.** The version classifier becomes a `MERGE` into the
versions table keyed on (system, record_id, ts_utc) with `WHEN NOT MATCHED
INSERT` only — duplicates no-op by key, stale rows are filtered by a join
against the per-encounter max timestamp before the merge. Ordering across
batches is enforced by processing batches serially per source (the registry
is the sequencer), which is exactly the local semantics.

**Schema evolution policy.** Contracts live in the repo as config (as here).
Bronze enforces them: a known signature maps, an unknown one fails the batch
with an alert — never `mergeSchema` on these feeds. Additive columns arrive
by config PR; renames are mapped, not propagated. Quarantine and audit
tables are the always-on feedback channel to source teams.

**Orchestration.** Databricks Workflows: per-source ingest task → validate →
clean/version → gate → publish → DQ report; retries with exponential
backoff on infra errors only (a manifest failure is terminal for that batch,
not retryable). Backfill/reprocess = replay from landing (immutable) into a
shadow catalog, validate counts vs audit, then swap — the pipeline is
deterministic from raw, which the idempotency test guarantees.

**SLAs & monitoring.** Batch landed→published SLA (e.g. 2h weekly); audit
and dq_results stream to dashboards; alerts on: manifest/gate failure,
error-share trend, schema-contract violation, stale-share spike, SLA breach.
Lakehouse monitoring on row-count drift per facility/system.

**Runbook — failed batch.** (1) Alert carries batch, file, reason. (2)
Manifest mismatch → confirm with `sha256sum` on landing copy; request
redelivery from the source team; nothing to clean up because nothing loaded.
(3) Schema violation → diff header vs contract; if announced change, PR the
new contract version, rerun batch; if not, escalate to source team. (4)
Gate failure → inspect dq_results + quarantine sample (lineage only), fix
config (e.g. new facility alias) or get redelivery, rerun. (5) Rerun is
always safe: the registry makes processed batches no-ops.

---

## 3. PHI governance in production

- **Access control:** Unity Catalog. Raw/bronze lives in a restricted
  catalog; only the pipeline service principal and a break-glass group can
  read it. Analysts and AI agents get gold only. (Snowflake: separate
  database + role hierarchy.)
- **Column controls:** masking policies on anything borderline in silver;
  row filters if facility-level access ever matters. Gold needs none —
  PHI never reaches it, by construction, with a CI test scanning outputs.
- **Audit:** account-level audit logs + UC lineage answer "who read raw and
  when"; quarantine/logs reference rows only by batch/file/row.
- **Encryption & keys:** platform encryption with customer-managed keys; the
  HMAC pepper lives in Azure Key Vault, injected at runtime, never in code
  or config. Key rotation: keep versioned peppers; rotating re-keys
  patient_key via a deterministic rebuild from raw (the pipeline is already
  a pure function of raw + secret), with a mapping table retained during the
  cutover window.
- **Minimum necessary:** the default surface (gold) carries age bands, zip3
  and pseudonymous keys only; re-identification requires raw access, which
  is the break-glass path with alerting.

---

## 4. Quality at scale — data contracts

Per source system, a versioned contract owned jointly with the source team:
file naming, delivery schedule, manifest spec (counts + SHA-256), header
signature, field semantics (date order, units, timezone — exactly what the
reference metadata encodes here), null conventions, and an SLA for
corrections. Contracts are code: schema signatures and mappings in the repo,
contract tests in CI, and a published quarantine/audit feed back to the
source team. A contract change arrives as a PR + a dated cutover, which is
precisely how the ATHENA v2 schema is handled in this repo.

## 5. Cost & performance

- **Partitioning/clustering:** versions and gold facts partitioned by admit
  month (the reporting axis), with arrival batch as a secondary predicate
  for as-of queries; liquid clustering on (source_system, source_record_id)
  for the merge path. Snowflake: cluster key on (admit_date) for the fact.
- **File sizing:** small weekly batches make small files — auto-compact /
  OPTIMIZE after publish targeting ~128MB–1GB files.
- **Compute:** serverless jobs for the weekly batch (minutes of compute);
  this is a small-data pipeline and the honest cost answer is "tiny" — the
  design spends engineering effort on correctness and governance, not
  cluster tuning. Reprocessing scales linearly by replaying landing.
