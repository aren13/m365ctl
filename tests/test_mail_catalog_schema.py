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


# A hand-crafted v1 mail_messages table — the pre-Phase-10.x shape that
# does NOT have ``cc_addresses``. Used to verify the v2 migration's ALTER
# TABLE adds the column non-destructively.
# Mirrors the pre-Phase-10.x v1 mail_messages shape (no cc_addresses).
# Includes columns referenced by v1 indexes so the v1 DDL re-runs cleanly
# under apply_schema (which is idempotent on tables but creates indexes).
_DDL_V1_MAIL_MESSAGES_LEGACY = """
CREATE TABLE IF NOT EXISTS mail_schema_meta (
    version INTEGER NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mail_messages (
    mailbox_upn          VARCHAR NOT NULL,
    message_id           VARCHAR NOT NULL,
    internet_message_id  VARCHAR,
    conversation_id      VARCHAR,
    parent_folder_id     VARCHAR,
    parent_folder_path   VARCHAR,
    subject              VARCHAR,
    from_address         VARCHAR,
    from_name            VARCHAR,
    to_addresses         VARCHAR,
    received_at          TIMESTAMP,
    sent_at              TIMESTAMP,
    is_read              BOOLEAN,
    is_draft             BOOLEAN,
    has_attachments      BOOLEAN,
    importance           VARCHAR,
    flag_status          VARCHAR,
    categories           VARCHAR,
    inference_class      VARCHAR,
    body_preview         VARCHAR,
    web_link             VARCHAR,
    size_estimate        BIGINT,
    is_deleted           BOOLEAN NOT NULL DEFAULT FALSE,
    last_seen_at         TIMESTAMP,
    PRIMARY KEY (mailbox_upn, message_id)
);
"""


def test_v1_to_v2_migration_adds_cc_addresses_column() -> None:
    conn = duckdb.connect(":memory:")
    # Stand up a v1-shaped DB by hand (no cc_addresses column).
    conn.execute(_DDL_V1_MAIL_MESSAGES_LEGACY)
    conn.execute(
        "INSERT INTO mail_messages (mailbox_upn, message_id, parent_folder_id, "
        "subject, to_addresses, received_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["me", "msg-old", "fld-1", "old", "x@y", "2026-01-01", "2026-01-01"],
    )
    cols_before = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'mail_messages'"
        ).fetchall()
    }
    assert "cc_addresses" not in cols_before

    apply_schema(conn)

    cols_after = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'mail_messages'"
        ).fetchall()
    }
    assert "cc_addresses" in cols_after
    # Pre-existing row's cc_addresses should be NULL.
    (cc,) = conn.execute(
        "SELECT cc_addresses FROM mail_messages WHERE message_id = 'msg-old'"
    ).fetchone()
    assert cc is None


def test_v2_apply_schema_idempotent() -> None:
    conn = duckdb.connect(":memory:")
    apply_schema(conn)
    apply_schema(conn)  # second call must be a no-op
    cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'mail_messages'"
        ).fetchall()
    }
    assert "cc_addresses" in cols


def test_fresh_db_gets_v2_schema_directly() -> None:
    conn = duckdb.connect(":memory:")
    apply_schema(conn)
    cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'mail_messages'"
        ).fetchall()
    }
    assert "cc_addresses" in cols
    assert "to_addresses" in cols


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
