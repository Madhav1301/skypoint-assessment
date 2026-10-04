# Data directory

The assessment data pack is **confidential** and therefore not committed to
this public repository (per the invitation email: "The brief and data pack
are confidential, so please do not share or publish them").

To run the pipeline, place the provided pack here so the layout is:

```
data/candidate_pack/
├── landing/
│   ├── batch_001/ ... batch_004/     (manifest.json + three encounter CSVs each)
└── reference/
    ├── source_systems_and_facilities.json
    ├── icd10_reference.csv
    └── provider_roster/roster_*.csv
```

Then, from the repository root:

```bash
cp .env.example .env
docker compose up --build
```

Nothing else is required: `docker-compose.yml` mounts `data/candidate_pack`
read-only at `/data` inside the container.
