from __future__ import annotations

from pathlib import Path

import pytest

from m365ctl.catalog.db import open_catalog


def test_open_catalog_creates_file_and_applies_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "catalog.duckdb"
    with open_catalog(db_path) as conn:
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert {"drives", "items", "schema_meta"} <= tables
    assert db_path.exists()


def test_open_catalog_creates_parent_dir(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "deep" / "catalog.duckdb"
    with open_catalog(db_path) as conn:
        conn.execute("SELECT 1").fetchone()
    assert db_path.exists()


def test_open_catalog_reuses_existing(tmp_path: Path) -> None:
    db_path = tmp_path / "catalog.duckdb"
    with open_catalog(db_path) as conn:
        conn.execute(
            "INSERT INTO items (drive_id, item_id, name, is_folder) "
            "VALUES ('d','i','x',false)"
        )
    with open_catalog(db_path) as conn:
        (n,) = conn.execute("SELECT COUNT(*) FROM items").fetchone()
        assert n == 1
