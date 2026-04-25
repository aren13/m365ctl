"""Convenience wrapper around the catalog ``top_senders`` query.

Adds a client-side ``--since`` filter without touching the underlying catalog
query API. Without ``since`` it delegates to the existing query.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.catalog.queries import top_senders


def top_senders_since(
    conn: duckdb.DuckDBPyConnection,
    *,
    mailbox_upn: str,
    since: datetime | None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return ``[{from_address, count}]`` for messages received after ``since``.

    When ``since`` is None, defers to the catalog ``top_senders`` query.
    """
    if since is None:
        return top_senders(conn, mailbox_upn=mailbox_upn, limit=limit)
    cur = conn.execute(
        """
        SELECT from_address, COUNT(*)::BIGINT AS count
        FROM mail_messages
        WHERE mailbox_upn = ? AND is_deleted = false
          AND from_address IS NOT NULL
          AND received_at >= ?
        GROUP BY from_address
        ORDER BY count DESC, from_address ASC
        LIMIT ?
        """,
        [mailbox_upn, since, limit],
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def build_top_senders(
    catalog_path: Path,
    *,
    mailbox_upn: str,
    since: datetime | None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """High-level wrapper used by the CLI: opens the catalog and runs the query.

    Returns ``[]`` if the catalog file does not exist.
    """
    if not catalog_path.exists():
        return []
    with open_catalog(catalog_path) as conn:
        return top_senders_since(
            conn, mailbox_upn=mailbox_upn, since=since, limit=limit,
        )


__all__ = ["top_senders_since", "build_top_senders"]
