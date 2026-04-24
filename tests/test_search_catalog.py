from __future__ import annotations

from pathlib import Path

from m365ctl.onedrive.catalog.db import open_catalog
from m365ctl.onedrive.search.catalog_search import catalog_search


def _seed(db: Path) -> None:
    with open_catalog(db) as conn:
        conn.execute(
            """
            INSERT INTO items (drive_id, item_id, name, full_path, is_folder,
                               is_deleted, size, modified_at, modified_by)
            VALUES
              ('d', '1', 'Invoice-Q1.pdf', '/Finance/Invoice-Q1.pdf', false, false, 100,
               TIMESTAMP '2024-06-01 00:00:00', 'a@example.com'),
              ('d', '2', 'Q2.xlsx',       '/Finance/Invoices/Q2.xlsx', false, false, 200,
               TIMESTAMP '2024-07-01 00:00:00', 'b@example.com'),
              ('d', '3', 'Readme.md',      '/Docs/Readme.md',          false, false, 50,
               TIMESTAMP '2024-01-01 00:00:00', 'a@example.com'),
              ('d', 'f', 'Finance',        '/Finance',                 true,  false, null,
               TIMESTAMP '2024-01-01 00:00:00', 'a@example.com'),
              ('d', 'g', 'Invoices',       '/Finance/Invoices',        true,  false, null,
               TIMESTAMP '2024-01-01 00:00:00', 'a@example.com'),
              ('d', 'x', 'old.pdf',        '/tomb/old.pdf',            false, true,  1,
               TIMESTAMP '2020-01-01 00:00:00', 'a@example.com')
            """
        )


def test_matches_name_case_insensitive(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        hits = list(catalog_search(conn, "invoice", type_="file"))
    names = {h.name for h in hits}
    assert "Invoice-Q1.pdf" in names
    # Q2.xlsx has 'invoices' in its path → matched via full_path LIKE
    assert "Q2.xlsx" in names
    # Folders excluded since type_='file'
    assert "Invoices" not in names


def test_type_folder_filters_to_folders(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        hits = list(catalog_search(conn, "invoice", type_="folder"))
    assert {h.name for h in hits} == {"Invoices"}


def test_type_all_returns_both(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        hits = list(catalog_search(conn, "invoice", type_="all"))
    names = {h.name for h in hits}
    assert {"Invoice-Q1.pdf", "Q2.xlsx", "Invoices"} <= names


def test_modified_since_filter(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        hits = list(catalog_search(conn, "invoice", type_="file",
                                   modified_since="2024-06-15"))
    assert {h.name for h in hits} == {"Q2.xlsx"}


def test_owner_filter(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        hits = list(catalog_search(conn, "invoice", type_="file",
                                   owner="b@example.com"))
    assert {h.name for h in hits} == {"Q2.xlsx"}


def test_scope_filter_drive(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        hits = list(catalog_search(conn, "invoice", type_="file",
                                   drive_ids=["d"]))
        assert any(h.drive_id == "d" for h in hits)
        hits_empty = list(catalog_search(conn, "invoice", type_="file",
                                         drive_ids=["other-drive"]))
        assert hits_empty == []


def test_excludes_deleted_by_default(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        hits = list(catalog_search(conn, "old.pdf", type_="file"))
    assert hits == []
