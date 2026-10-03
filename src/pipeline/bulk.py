"""Fast bulk inserts into DuckDB via a temp CSV + read_csv.

duckdb's executemany prepares and binds row by row (~15 ms/row on the raw
table); routing the same rows through a temp CSV and one vectorised
INSERT .. SELECT read_csv(..) is orders of magnitude faster and adds no
dependencies. NULL is encoded as \\N so empty strings survive verbatim —
the raw layer must store fields exactly as delivered.
"""

from __future__ import annotations

import csv
import os
import tempfile
from datetime import date, datetime
from decimal import Decimal

_NULL = "\\N"


def _serialize(value) -> str:
    if value is None:
        return _NULL
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (int, Decimal)):
        return str(value)
    return str(value)


def bulk_insert(con, table: str, columns: dict[str, str], rows) -> int:
    """Insert rows (iterable of tuples, ordered like `columns`) into table.

    columns: ordered {column_name: duckdb_type}. Returns the row count.
    """
    rows = list(rows)
    if not rows:
        return 0
    fd, path = tempfile.mkstemp(suffix=".csv")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            for row in rows:
                writer.writerow([_serialize(v) for v in row])
        columns_struct = ", ".join(f"'{name}': '{dtype}'" for name, dtype in columns.items())
        column_list = ", ".join(columns.keys())
        con.execute(
            f"INSERT INTO {table} ({column_list}) "
            f"SELECT * FROM read_csv('{path.replace(chr(92), '/')}', header=false, "
            f"nullstr='{_NULL}', quote='\"', escape='\"', delim=',', "
            f"columns={{{columns_struct}}})"
        )
        return len(rows)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
