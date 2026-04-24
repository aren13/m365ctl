"""Canned queries over the mail catalog.

All queries scope by ``mailbox_upn`` and exclude soft-deleted rows
(``is_deleted = false``) by default. Results are plain dicts so callers
can emit JSON, TSV, or pretty-print.
"""
from __future__ import annotations

from typing import Any

import duckdb

_LIVE_WHERE = "mailbox_upn = ? AND is_deleted = false"


def _rows_as_dicts(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def unread_in_folder(
    conn: duckdb.DuckDBPyConnection, *, mailbox_upn: str, folder_id: str,
) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"""
        SELECT message_id, subject, from_address, received_at, body_preview
        FROM mail_messages
        WHERE {_LIVE_WHERE}
          AND parent_folder_id = ?
          AND is_read = false
        ORDER BY received_at DESC
        """,
        [mailbox_upn, folder_id],
    )
    return _rows_as_dicts(cur)


def older_than(
    conn: duckdb.DuckDBPyConnection, *, mailbox_upn: str, cutoff: str,
) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"""
        SELECT message_id, subject, from_address, received_at, parent_folder_path
        FROM mail_messages
        WHERE {_LIVE_WHERE}
          AND received_at < CAST(? AS TIMESTAMP)
        ORDER BY received_at ASC
        """,
        [mailbox_upn, cutoff],
    )
    return _rows_as_dicts(cur)


def by_sender(
    conn: duckdb.DuckDBPyConnection, *, mailbox_upn: str, sender: str,
) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"""
        SELECT message_id, subject, from_address, received_at, parent_folder_path
        FROM mail_messages
        WHERE {_LIVE_WHERE}
          AND from_address = ?
        ORDER BY received_at DESC
        """,
        [mailbox_upn, sender],
    )
    return _rows_as_dicts(cur)


def attachments_by_size(
    conn: duckdb.DuckDBPyConnection, *, mailbox_upn: str, min_bytes: int,
) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"""
        SELECT message_id, subject, from_address, received_at, size_estimate
        FROM mail_messages
        WHERE {_LIVE_WHERE}
          AND has_attachments = true
          AND size_estimate >= ?
        ORDER BY size_estimate DESC
        """,
        [mailbox_upn, min_bytes],
    )
    return _rows_as_dicts(cur)


def top_senders(
    conn: duckdb.DuckDBPyConnection, *, mailbox_upn: str, limit: int = 20,
) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"""
        SELECT from_address, COUNT(*)::BIGINT AS count
        FROM mail_messages
        WHERE {_LIVE_WHERE}
          AND from_address IS NOT NULL
        GROUP BY from_address
        ORDER BY count DESC, from_address ASC
        LIMIT ?
        """,
        [mailbox_upn, limit],
    )
    return _rows_as_dicts(cur)


def size_per_folder(
    conn: duckdb.DuckDBPyConnection, *, mailbox_upn: str,
) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"""
        SELECT parent_folder_path,
               COUNT(*)::BIGINT AS message_count,
               COALESCE(SUM(size_estimate), 0)::BIGINT AS total_size
        FROM mail_messages
        WHERE {_LIVE_WHERE}
        GROUP BY parent_folder_path
        ORDER BY total_size DESC
        """,
        [mailbox_upn],
    )
    return _rows_as_dicts(cur)


def _scalar(cur: duckdb.DuckDBPyConnection) -> Any:
    """First-column scalar of a single-row query (e.g. COUNT(*), MAX(...))."""
    row = cur.fetchone()
    assert row is not None, "aggregate query must return one row"
    return row[0]


def summary(
    conn: duckdb.DuckDBPyConnection, *, mailbox_upn: str,
) -> dict[str, Any]:
    alive = _scalar(conn.execute(
        f"SELECT COUNT(*) FROM mail_messages WHERE {_LIVE_WHERE}",
        [mailbox_upn],
    ))
    deleted = _scalar(conn.execute(
        "SELECT COUNT(*) FROM mail_messages "
        "WHERE mailbox_upn = ? AND is_deleted = true",
        [mailbox_upn],
    ))
    folders = _scalar(conn.execute(
        "SELECT COUNT(*) FROM mail_folders WHERE mailbox_upn = ?",
        [mailbox_upn],
    ))
    last_refreshed = _scalar(conn.execute(
        "SELECT MAX(last_refreshed_at) FROM mail_deltas WHERE mailbox_upn = ?",
        [mailbox_upn],
    ))
    return {
        "messages_total": alive,
        "messages_deleted": deleted,
        "folders_total": folders,
        "last_refreshed_at": last_refreshed,
    }
