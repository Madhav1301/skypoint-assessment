# AI usage log

AI assistant: Claude Code (Anthropic), used throughout. This log is honest
about where it saved time, what it got wrong, and where I overrode it. Every
line of the result is mine to defend: the design was written down first (an
approach document with the layer design, error taxonomy, assumptions and
test plan), and generated code was reviewed against it.

## Where it saved time

- **Profiling before any code.** Scripted a full survey of the pack:
  manifest verification (found batch_004's truncated EPIC file), per-batch
  header diffs (found the ATHENA v2 schema change in batch_003), value
  frequency tables (all 23 encounter-type spellings, the `$67.53K` amounts,
  MEDITECH cents), Luhn checks (the four corrupted NPIs and their roster
  counterparts), and the timezone analysis that showed 20 encounters flip
  their current version under naive timestamp ordering. That last finding
  shaped the whole versioning design.
- **Test-case enumeration.** Unit-test inputs are lifted from real observed
  values rather than invented ones, which is why the suite (256 tests)
  covers every planted trap.
- **Boilerplate at the edges.** Docker/Compose, config plumbing, markdown
  rendering of DQ reports.
- **The bulk-load fix.** Diagnosed duckdb `executemany` as the bottleneck
  (~15 ms/row) and routed inserts through a temp CSV + `read_csv`, taking
  the full pipeline from ~55 s to ~3 s.

## What it got wrong / where I overrode it

- **Invalid git flags.** It scripted `git cherry-pick -q`, which doesn't
  exist; the history rewrite half-applied and had to be repaired from the
  old commit hashes.
- **A test that defeated its own purpose.** The first version of the
  duplicate/stale test used a 4-row batch with one bad row — 25% error share
  — which correctly tripped the publish gate the test wasn't about. The
  fixture was redesigned to sit under the threshold.
- **Pytest import assumptions.** Generated tests imported helpers from
  `conftest` across directories, which pytest's path rules don't allow;
  reworked into fixtures.
- **A missing dependency only tests caught.** Fetching TIMESTAMPTZ values
  from DuckDB requires `pytz`, which wasn't pinned until the versioning
  tests failed.
- **Noisy DQ taxonomy.** The first clean layer flagged every blank discharge
  date as a warning — 41 flags in batch_003 alone for what is the normal
  state of ambulatory visits. Overrode to record the reason code without
  counting it as a quality warning, and documented the call in dq_rules.
- **Dead code and meaningless assertions** occasionally slipped into drafts
  (an unused error branch in the cleaning module, an always-true regex
  assertion in a PHI test); both caught in review and removed.

## Judgment calls that were explicitly human

The mapping semantics (`Cancelled` → VOID, TRICARE → OTHER), the 10% gate
threshold, alias-table-over-fuzzy facility resolution, the stale-row
exclusion semantics (A14), the ICD-9 vs dotless-E-code ruling (A10), the
DST fold choice (A11), fail-closed export filters, and the decision not to
commit the DuckDB file (raw layer contains PHI) were design decisions made
and documented before implementation — the assistant implemented them.
