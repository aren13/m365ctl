from __future__ import annotations

import duckdb

from fazla_od.catalog.schema import CURRENT_SCHEMA_VERSION, apply_schema


def test_apply_schema_creates_expected_tables() -> None:
    conn = duckdb.connect(":memory:")
    apply_schema(conn)

    tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    assert {"drives", "items", "schema_meta"} <= tables


def test_apply_schema_records_version() -> None:
    conn = duckdb.connect(":memory:")
    apply_schema(conn)
    (version,) = conn.execute(
        "SELECT version FROM schema_meta ORDER BY applied_at DESC LIMIT 1"
    ).fetchone()
    assert version == CURRENT_SCHEMA_VERSION


def test_apply_schema_is_idempotent() -> None:
    conn = duckdb.connect(":memory:")
    apply_schema(conn)
    apply_schema(conn)
    (n,) = conn.execute("SELECT COUNT(*) FROM schema_meta").fetchone()
    assert n == 1  # second apply is a no-op


def test_items_table_has_primary_key_on_drive_item() -> None:
    conn = duckdb.connect(":memory:")
    apply_schema(conn)
    conn.execute(
        "INSERT INTO items (drive_id, item_id, name, is_folder, is_deleted) "
        "VALUES ('d', 'i', 'x', false, false)"
    )
    # Upsert-style replacement should work via ON CONFLICT
    conn.execute(
        """
        INSERT INTO items (drive_id, item_id, name, is_folder, is_deleted)
        VALUES ('d', 'i', 'y', false, false)
        ON CONFLICT (drive_id, item_id) DO UPDATE SET name = EXCLUDED.name
        """
    )
    (name,) = conn.execute(
        "SELECT name FROM items WHERE drive_id='d' AND item_id='i'"
    ).fetchone()
    assert name == "y"
