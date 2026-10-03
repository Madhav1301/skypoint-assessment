# Northwind Health Encounter Pipeline

A local, containerised data pipeline for the Skypoint Data Engineering
Assessment: it ingests weekly batches of messy healthcare encounter CSVs from
three EHR source systems, validates them against their manifests, cleans and
de-identifies them, and models a small governed warehouse with full lineage.

> **Status: work in progress.** Task 1 (ingest & validate) is implemented;
> cleaning, PHI protection, versioning, the warehouse model and the required
> export land in subsequent commits. This README grows with the code.

## How to run

```bash
git clone <this-repo>
cd skypoint-assessment
cp .env.example .env
docker compose up --build
```

Processes `batch_001` → `batch_004` in order and writes every output CSV to
`./output`. The deliberately corrupted `batch_004` is rejected and reported in
`output/batch_audit.csv` — the run still exits 0.

## How to run the tests

```bash
docker compose run --rm tests
```

Without Docker (local Python 3.12):

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
.venv/Scripts/python -m pytest -q
```

## Layout

```
config/    schema contracts, value mappings, facility aliases, DQ rules (YAML)
src/       pipeline package (ingestion today; cleaning/model next)
tests/     unit tests + integration tests against the real data pack
data/      the candidate data pack (mounted read-only in Docker)
output/    generated CSVs + the DuckDB warehouse file
```

## Documentation

- `ARCHITECTURE.md` — data model, production design, PHI governance (in progress)
- `AI_USAGE.md` — where AI coding tools were used, what they got wrong
