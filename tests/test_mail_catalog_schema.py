from __future__ import annotations

import duckdb

from m365ctl.mail.catalog.schema import CURRENT_SCHEMA_VERSION, apply_schema


def test_apply_schema_creates_all_tables() -> None:
    conn = duckdb.connect(":memory:")
    apply_schema(conn)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }
    assert {
        "mail_schema_meta",
        "mail_folders",
        "mail_messages",
        "mail_categories",
        "mail_deltas",
    } <= tables


def test_apply_schema_records_version_once() -> None:
    conn = duckdb.connect(":memory:")
    apply_schema(conn)
    apply_schema(conn)  # idempotent
    (count,) = conn.execute(
        "SELECT COUNT(*) FROM mail_schema_meta WHERE version = ?",
        [CURRENT_SCHEMA_VERSION],
    ).fetchone()
    assert count == 1


def test_messages_pk_is_composite() -> None:
    conn = duckdb.connect(":memory:")
    apply_schema(conn)
    # Insert two rows that differ only by mailbox_upn — both should succeed.
    for upn in ("a@example.com", "b@example.com"):
        conn.execute(
            "INSERT INTO mail_messages (mailbox_upn, message_id, parent_folder_id, "
            "subject, received_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
            [upn, "msg-1", "fld-1", "x", "2026-01-01", "2026-01-01"],
        )
    (n,) = conn.execute("SELECT COUNT(*) FROM mail_messages").fetchone()
    assert n == 2
