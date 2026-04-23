"""Canned inventory queries over the catalog.

All queries exclude folders and soft-deleted items by default. Results are
returned as lists of plain dicts so callers can emit TSV/JSON/whatever.
"""
from __future__ import annotations

from typing import Any

import duckdb

_LIVE_FILE_WHERE = "is_folder = false AND is_deleted = false"


def _rows_as_dicts(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def top_by_size(conn: duckdb.DuckDBPyConnection, *, limit: int) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"""
        SELECT drive_id, item_id, name, full_path, size, modified_at,
               modified_by, is_folder
        FROM items
        WHERE {_LIVE_FILE_WHERE}
          AND size IS NOT NULL
        ORDER BY size DESC
        LIMIT ?
        """,
        [limit],
    )
    return _rows_as_dicts(cur)


def stale_since(
    conn: duckdb.DuckDBPyConnection, *, cutoff: str
) -> list[dict[str, Any]]:
    """Items not modified since ``cutoff`` (ISO date, e.g. '2024-01-01')."""
    cur = conn.execute(
        f"""
        SELECT drive_id, item_id, name, full_path, size, modified_at, modified_by
        FROM items
        WHERE {_LIVE_FILE_WHERE}
          AND modified_at < CAST(? AS TIMESTAMP)
        ORDER BY modified_at ASC
        """,
        [cutoff],
    )
    return _rows_as_dicts(cur)


def by_owner(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"""
        SELECT modified_by AS owner,
               COUNT(*)::BIGINT AS file_count,
               COALESCE(SUM(size), 0)::BIGINT AS total_size
        FROM items
        WHERE {_LIVE_FILE_WHERE}
        GROUP BY modified_by
        ORDER BY total_size DESC
        """
    )
    return _rows_as_dicts(cur)


def duplicates(
    conn: duckdb.DuckDBPyConnection, *, min_group_size: int = 2
) -> list[dict[str, Any]]:
    """Items sharing a quick_xor_hash with at least ``min_group_size`` members.

    Returns one row per item (not one per group) so callers can see every
    duplicate; group membership is implied by shared ``quick_xor_hash``.
    """
    cur = conn.execute(
        f"""
        WITH groups AS (
            SELECT quick_xor_hash
            FROM items
            WHERE {_LIVE_FILE_WHERE} AND quick_xor_hash IS NOT NULL
            GROUP BY quick_xor_hash
            HAVING COUNT(*) >= ?
        )
        SELECT i.drive_id, i.item_id, i.name, i.full_path, i.size,
               i.modified_at, i.quick_xor_hash
        FROM items i
        JOIN groups g USING (quick_xor_hash)
        WHERE i.is_folder = false AND i.is_deleted = false
        ORDER BY i.quick_xor_hash, i.size DESC
        """,
        [min_group_size],
    )
    return _rows_as_dicts(cur)
