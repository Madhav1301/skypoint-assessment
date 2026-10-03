"""Every ```sql block in README.md must execute and return rows.

The six required query patterns are documented as runnable SQL; this test
runs them verbatim against a freshly built warehouse so the README cannot
rot as the schema evolves.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pipeline import db
from pipeline.ingest import process_batch
from pipeline.model import build_gold, build_reference
from pipeline.versioning import VersionState

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "candidate_pack"
README = REPO_ROOT / "README.md"

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "landing").exists(), reason="data pack not available"
)

SQL_BLOCK_RE = re.compile(r"```sql\n(.*?)```", re.DOTALL)


def readme_sql_blocks() -> list[str]:
    return SQL_BLOCK_RE.findall(README.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def con(tmp_path_factory, run_cfg):
    workdir = tmp_path_factory.mktemp("readme")
    connection = db.connect(workdir / "wh.duckdb")
    state = VersionState.load(connection)
    for batch_dir in sorted((DATA_DIR / "landing").iterdir()):
        if batch_dir.is_dir():
            process_batch(connection, batch_dir, run_cfg, state)
    build_reference(connection, DATA_DIR)
    build_gold(connection)
    yield connection
    connection.close()


def test_readme_contains_the_six_query_patterns():
    assert len(readme_sql_blocks()) == 6


@pytest.mark.parametrize("index", range(6))
def test_readme_query_runs_and_returns_rows(con, index):
    blocks = readme_sql_blocks()
    rows = con.execute(blocks[index]).fetchall()
    assert len(rows) > 0, f"README query #{index + 1} returned no rows"
