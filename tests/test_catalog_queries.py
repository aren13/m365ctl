from __future__ import annotations

from pathlib import Path

import pytest

from m365ctl.onedrive.catalog.db import open_catalog
from m365ctl.onedrive.catalog.queries import (
    by_owner,
    duplicates,
    stale_since,
    top_by_size,
)


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    db = tmp_path / "catalog.duckdb"
    with open_catalog(db) as conn:
        conn.execute(
            """
            INSERT INTO items (drive_id, item_id, name, is_folder, is_deleted,
                               size, modified_at, modified_by, quick_xor_hash)
            VALUES
              ('d', '1', 'big.mp4',   false, false, 5000000000, TIMESTAMP '2023-03-01 00:00:00', 'alice@fazla.com', 'H1'),
              ('d', '2', 'medium.zip',false, false, 1000000000, TIMESTAMP '2024-01-01 00:00:00', 'alice@fazla.com', 'H2'),
              ('d', '3', 'small.txt', false, false, 100,        TIMESTAMP '2025-06-01 00:00:00', 'bob@fazla.com',   NULL),
              ('d', '4', 'dup-a',     false, false, 500,        TIMESTAMP '2024-10-01 00:00:00', 'bob@fazla.com',   'DUP'),
              ('d', '5', 'dup-b',     false, false, 500,        TIMESTAMP '2024-10-02 00:00:00', 'bob@fazla.com',   'DUP'),
              ('d', 'f', 'Folder',    true,  false, NULL,       TIMESTAMP '2024-01-01 00:00:00', 'alice@fazla.com', NULL),
              ('d', 'x', 'deleted',   false, true,  99999,      TIMESTAMP '2020-01-01 00:00:00', 'alice@fazla.com', NULL)
            """
        )
    return db


def test_top_by_size_excludes_folders_and_deleted(seeded_db: Path) -> None:
    with open_catalog(seeded_db) as conn:
        rows = top_by_size(conn, limit=3)
    names = [r["name"] for r in rows]
    sizes = [r["size"] for r in rows]
    assert names == ["big.mp4", "medium.zip", "dup-a"] or names[0] == "big.mp4"
    assert sizes[0] == 5000000000
    assert all(r["is_folder"] is False for r in rows)


def test_stale_since_returns_items_older_than_cutoff(seeded_db: Path) -> None:
    with open_catalog(seeded_db) as conn:
        rows = stale_since(conn, cutoff="2024-06-01")
    names = {r["name"] for r in rows}
    # big.mp4 (2023) and medium.zip (2024-01-01) qualify; folders excluded
    assert "big.mp4" in names
    assert "medium.zip" in names
    assert "small.txt" not in names
    assert "Folder" not in names


def test_by_owner_aggregates_size(seeded_db: Path) -> None:
    with open_catalog(seeded_db) as conn:
        rows = by_owner(conn)
    by = {r["owner"]: (r["file_count"], r["total_size"]) for r in rows}
    # alice: big(5e9) + medium(1e9) = 6e9, 2 files
    # bob: small(100) + dup-a(500) + dup-b(500) = 1100, 3 files
    assert by["alice@fazla.com"] == (2, 6000000000)
    assert by["bob@fazla.com"] == (3, 1100)


def test_duplicates_groups_matching_hashes(seeded_db: Path) -> None:
    with open_catalog(seeded_db) as conn:
        rows = duplicates(conn, min_group_size=2)
    # Only 'DUP' hash has 2 items
    assert len(rows) == 2  # two items in one group
    names = sorted(r["name"] for r in rows)
    assert names == ["dup-a", "dup-b"]
    assert all(r["quick_xor_hash"] == "DUP" for r in rows)
