# AI usage log

AI assistant: Claude Code (Anthropic). This log is kept honest and updated as
the build progresses: where it saved time, what it got wrong, where I overrode
it.

## Where it saved time

- **Data profiling before any code**: scripted a full profile of the pack
  (manifest verification, header diffs per batch, value-pattern frequency
  tables). This surfaced the planted traps early: batch_004's truncated EPIC
  file, the ATHENA schema change in batch_003, the timezone-dependent version
  ordering, `TEST FACILITY - DO NOT USE`, and `Westfield Surgical Center`.
- **Scaffolding**: Docker/Compose, config layout and the ingestion skeleton
  were generated from an agreed written design, then reviewed line by line.
- **Test case enumeration**: drafting unit test inputs from real observed
  values (e.g. `$2.07K`, `1,683,845` cents, `2024-02-30`).

## What it got wrong / where I overrode it

- (tracked as they occur)

## Human judgement calls

All modelling decisions, mapping semantics (e.g. `Cancelled` → VOID), the
publish-gate threshold, and the alias-table-over-fuzzy-matching choice were
made deliberately and documented in the approach doc and `config/*.yaml`
comments — not accepted blindly from a generator.
