# Architecture

> Skeleton — filled in as the corresponding components land.

## 1. Data model
Grain, keys and relationships of every table; SCD2 provider dimension;
history vs current state; as-of reporting by arrival batch.

## 2. Production design (Azure Databricks / Snowflake)
Ingestion from landing storage, layer design, ordered upserts, schema
evolution policy, orchestration, backfill and reprocessing, SLAs, monitoring,
alerting, runbook for a failed batch.

## 3. PHI governance
Access control, column masking, audit, encryption, key management,
minimum-necessary access.

## 4. Quality at scale
Data contracts with source teams.

## 5. Cost and performance
Partitioning / clustering, file sizing, compute choices.

## 6. Delivery
Environments, CI/CD, testing strategy.

## 7. Planning the build
Six-week plan across three engineers; PR review checklist; top risks.
