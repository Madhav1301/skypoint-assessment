"""Run the README's example queries (or ad-hoc SQL) against the warehouse.

Usage:
    python scripts/query.py              list the six README query patterns
    python scripts/query.py 2            run README query #2
    python scripts/query.py --sql "SELECT count(*) FROM gold.fact_encounter_current"
    python scripts/query.py 1 --db output/warehouse.duckdb

Exists because quoting multi-line SQL on a Windows command line is painful;
this needs no escaping at all.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
_SQL_BLOCK_RE = re.compile(r"```sql\n(.*?)```", re.DOTALL)


def readme_queries() -> list[str]:
    return _SQL_BLOCK_RE.findall((REPO_ROOT / "README.md").read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run README example queries against the DuckDB warehouse."
    )
    parser.add_argument("number", nargs="?", type=int,
                        help="README query number (1-6); omit to list them")
    parser.add_argument("--sql", help="ad-hoc SQL to run instead of a README query")
    parser.add_argument("--db", default=str(REPO_ROOT / "output" / "warehouse.duckdb"),
                        help="path to the warehouse file (default: output/warehouse.duckdb)")
    args = parser.parse_args()

    queries = readme_queries()
    if args.sql:
        sql = args.sql
    elif args.number is not None:
        if not 1 <= args.number <= len(queries):
            sys.exit(f"Pick a query number between 1 and {len(queries)}.")
        sql = queries[args.number - 1]
    else:
        print("Available README queries (run with: python scripts/query.py <n>):\n")
        for i, q in enumerate(queries, start=1):
            first_line = q.strip().splitlines()[0]
            print(f"  {i}: {first_line[:110]}")
        return

    if not Path(args.db).exists():
        sys.exit(f"Warehouse not found at {args.db} - run the pipeline first.")
    con = duckdb.connect(args.db, read_only=True)
    con.sql("SET timezone = 'UTC'")  # display instants as stored, not machine-local
    con.sql(sql).show()


if __name__ == "__main__":
    main()
