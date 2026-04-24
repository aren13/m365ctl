"""Adapter: DuckDB catalog -> SearchHit (name + full_path LIKE match)."""
from __future__ import annotations

from typing import Iterator, Literal

import duckdb

from fazla_od.search.graph_search import SearchHit

Type = Literal["file", "folder", "all"]


def catalog_search(
    conn: duckdb.DuckDBPyConnection,
    query: str,
    *,
    type_: Type = "file",
    modified_since: str | None = None,
    owner: str | None = None,
    drive_ids: list[str] | None = None,
) -> Iterator[SearchHit]:
    where: list[str] = ["is_deleted = false"]
    params: list[object] = []

    if type_ == "file":
        where.append("is_folder = false")
    elif type_ == "folder":
        where.append("is_folder = true")

    where.append("(LOWER(name) LIKE LOWER(?) OR LOWER(full_path) LIKE LOWER(?))")
    like = f"%{query}%"
    params.extend([like, like])

    if modified_since:
        where.append("modified_at >= CAST(? AS TIMESTAMP)")
        params.append(modified_since)
    if owner:
        where.append("modified_by = ?")
        params.append(owner)
    if drive_ids:
        placeholders = ",".join(["?"] * len(drive_ids))
        where.append(f"drive_id IN ({placeholders})")
        params.extend(drive_ids)

    sql = f"""
        SELECT drive_id, item_id, name, full_path, size,
               CAST(modified_at AS VARCHAR) AS modified_at,
               modified_by, is_folder
        FROM items
        WHERE {' AND '.join(where)}
        ORDER BY modified_at DESC NULLS LAST
    """
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        rec = dict(zip(cols, row))
        yield SearchHit(
            drive_id=rec["drive_id"],
            item_id=rec["item_id"],
            name=rec["name"],
            full_path=rec["full_path"],
            size=rec["size"],
            modified_at=rec["modified_at"],
            modified_by=rec["modified_by"],
            is_folder=bool(rec["is_folder"]),
            source="catalog",
        )
