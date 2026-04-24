"""DuckDB schema for the m365ctl catalog.

One-shot migration: on connection, call ``apply_schema(conn)``. It creates
the tables if missing and records the applied version in ``schema_meta``.
Future plans bump ``CURRENT_SCHEMA_VERSION`` and add branches.
"""
from __future__ import annotations

import duckdb

CURRENT_SCHEMA_VERSION = 1

_DDL_V1 = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS drives (
    drive_id       VARCHAR PRIMARY KEY,
    display_name   VARCHAR,
    owner          VARCHAR,
    drive_type     VARCHAR,
    delta_link     VARCHAR,
    last_refreshed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS items (
    drive_id        VARCHAR NOT NULL,
    item_id         VARCHAR NOT NULL,
    name            VARCHAR NOT NULL,
    parent_path     VARCHAR,
    full_path       VARCHAR,
    size            BIGINT,
    mime_type       VARCHAR,
    is_folder       BOOLEAN NOT NULL,
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMP,
    modified_at     TIMESTAMP,
    created_by      VARCHAR,
    modified_by     VARCHAR,
    has_sharing     BOOLEAN,
    quick_xor_hash  VARCHAR,
    etag            VARCHAR,
    last_seen_at    TIMESTAMP,
    PRIMARY KEY (drive_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_items_size     ON items(size);
CREATE INDEX IF NOT EXISTS idx_items_modified ON items(modified_at);
CREATE INDEX IF NOT EXISTS idx_items_parent   ON items(drive_id, parent_path);
CREATE INDEX IF NOT EXISTS idx_items_hash     ON items(quick_xor_hash);
"""


def apply_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(_DDL_V1)
    row = conn.execute(
        "SELECT COUNT(*) FROM schema_meta WHERE version = ?",
        [CURRENT_SCHEMA_VERSION],
    ).fetchone()
    assert row is not None
    (already,) = row
    if already == 0:
        conn.execute(
            "INSERT INTO schema_meta (version) VALUES (?)",
            [CURRENT_SCHEMA_VERSION],
        )
