"""DuckDB connection helper for the catalog."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from m365ctl.catalog.schema import apply_schema


@contextmanager
def open_catalog(path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))
    try:
        apply_schema(conn)
        yield conn
    finally:
        conn.close()
