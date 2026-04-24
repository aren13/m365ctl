"""DuckDB schema for the mail catalog.

One-shot migration: ``apply_schema(conn)`` creates the tables if missing
and records the version in ``mail_schema_meta``. Future plans bump
``CURRENT_SCHEMA_VERSION`` and add branches.

Composite PKs always lead with ``mailbox_upn`` so the catalog can hold
multiple mailboxes side-by-side once Phase 12 (delegation) lands.
"""
from __future__ import annotations

import duckdb

CURRENT_SCHEMA_VERSION = 1

_DDL_V1 = """
CREATE TABLE IF NOT EXISTS mail_schema_meta (
    version INTEGER NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mail_folders (
    mailbox_upn        VARCHAR NOT NULL,
    folder_id          VARCHAR NOT NULL,
    display_name       VARCHAR,
    parent_folder_id   VARCHAR,
    path               VARCHAR,
    well_known_name    VARCHAR,
    total_items        INTEGER,
    unread_items       INTEGER,
    child_folder_count INTEGER,
    last_seen_at       TIMESTAMP,
    PRIMARY KEY (mailbox_upn, folder_id)
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
    to_addresses         VARCHAR,   -- comma-joined for cheap LIKE search
    received_at          TIMESTAMP,
    sent_at              TIMESTAMP,
    is_read              BOOLEAN,
    is_draft             BOOLEAN,
    has_attachments      BOOLEAN,
    importance           VARCHAR,
    flag_status          VARCHAR,
    categories           VARCHAR,   -- comma-joined
    inference_class      VARCHAR,
    body_preview         VARCHAR,
    web_link             VARCHAR,
    size_estimate        BIGINT,    -- bodyPreview + attachment sum approx
    is_deleted           BOOLEAN NOT NULL DEFAULT FALSE,
    last_seen_at         TIMESTAMP,
    PRIMARY KEY (mailbox_upn, message_id)
);

CREATE INDEX IF NOT EXISTS idx_mail_messages_received
    ON mail_messages(mailbox_upn, received_at);
CREATE INDEX IF NOT EXISTS idx_mail_messages_from
    ON mail_messages(mailbox_upn, from_address);
CREATE INDEX IF NOT EXISTS idx_mail_messages_folder_unread
    ON mail_messages(mailbox_upn, parent_folder_id, is_read);

CREATE TABLE IF NOT EXISTS mail_categories (
    mailbox_upn  VARCHAR NOT NULL,
    category_id  VARCHAR NOT NULL,
    display_name VARCHAR,
    color        VARCHAR,
    last_seen_at TIMESTAMP,
    PRIMARY KEY (mailbox_upn, category_id)
);

CREATE TABLE IF NOT EXISTS mail_deltas (
    mailbox_upn        VARCHAR NOT NULL,
    folder_id          VARCHAR NOT NULL,
    delta_link         VARCHAR,
    last_refreshed_at  TIMESTAMP,
    last_status        VARCHAR,    -- 'ok' | 'restarted' | 'failed'
    PRIMARY KEY (mailbox_upn, folder_id)
);
"""


def apply_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(_DDL_V1)
    (already,) = conn.execute(
        "SELECT COUNT(*) FROM mail_schema_meta WHERE version = ?",
        [CURRENT_SCHEMA_VERSION],
    ).fetchone()
    if already == 0:
        conn.execute(
            "INSERT INTO mail_schema_meta (version) VALUES (?)",
            [CURRENT_SCHEMA_VERSION],
        )
