"""Delta crawler: pulls items from Graph into the DuckDB catalog."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from fazla_od.catalog.normalize import normalize_item


class _GraphLike(Protocol):
    def get(self, path: str, *, params: dict | None = ...) -> dict: ...
    def get_paginated(self, path: str, *, params: dict | None = ...): ...


@dataclass(frozen=True)
class DriveSpec:
    drive_id: str
    display_name: str
    owner: str
    drive_type: str
    graph_path: str  # first-call delta path, e.g. "/me/drive/root/delta"


@dataclass(frozen=True)
class CrawlResult:
    drive_id: str
    items_seen: int
    delta_link: str | None


def resolve_scope(scope: str, graph: _GraphLike) -> list[DriveSpec]:
    """Translate a scope string into one or more DriveSpecs.

    Plan 2 supports only 'me' and 'drive:<id>'. 'site:<slug>' and 'tenant'
    are Plan 3.
    """
    if scope == "me":
        meta = graph.get("/me/drive")
        return [
            DriveSpec(
                drive_id=meta["id"],
                display_name=meta.get("name", "OneDrive"),
                owner=_owner_of(meta),
                drive_type=meta.get("driveType", "unknown"),
                graph_path="/me/drive/root/delta",
            )
        ]
    if scope.startswith("drive:"):
        drive_id = scope.split(":", 1)[1]
        meta = graph.get(f"/drives/{drive_id}")
        return [
            DriveSpec(
                drive_id=meta["id"],
                display_name=meta.get("name", drive_id),
                owner=_owner_of(meta),
                drive_type=meta.get("driveType", "unknown"),
                graph_path=f"/drives/{drive_id}/root/delta",
            )
        ]
    raise ValueError(f"unknown scope: {scope!r}")


def _owner_of(drive_meta: dict) -> str:
    owner = drive_meta.get("owner") or {}
    user = owner.get("user") or {}
    return user.get("email") or user.get("displayName") or "unknown"


_UPSERT_ITEM_SQL = """
INSERT INTO items (
    drive_id, item_id, name, parent_path, full_path, size, mime_type,
    is_folder, is_deleted, created_at, modified_at, created_by, modified_by,
    has_sharing, quick_xor_hash, etag, last_seen_at
) VALUES (
    $drive_id, $item_id, $name, $parent_path, $full_path, $size, $mime_type,
    $is_folder, $is_deleted, $created_at, $modified_at, $created_by,
    $modified_by, $has_sharing, $quick_xor_hash, $etag, $last_seen_at
)
ON CONFLICT (drive_id, item_id) DO UPDATE SET
    name = EXCLUDED.name,
    parent_path = EXCLUDED.parent_path,
    full_path = EXCLUDED.full_path,
    size = EXCLUDED.size,
    mime_type = EXCLUDED.mime_type,
    is_folder = EXCLUDED.is_folder,
    is_deleted = EXCLUDED.is_deleted,
    created_at = EXCLUDED.created_at,
    modified_at = EXCLUDED.modified_at,
    created_by = EXCLUDED.created_by,
    modified_by = EXCLUDED.modified_by,
    has_sharing = EXCLUDED.has_sharing,
    quick_xor_hash = EXCLUDED.quick_xor_hash,
    etag = EXCLUDED.etag,
    last_seen_at = EXCLUDED.last_seen_at
"""


def crawl_drive(
    drive: DriveSpec, graph: _GraphLike, conn
) -> CrawlResult:
    """Crawl one drive into the catalog, resuming from stored deltaLink if any."""
    row = conn.execute(
        "SELECT delta_link FROM drives WHERE drive_id = ?", [drive.drive_id]
    ).fetchone()
    stored_link = row[0] if row else None
    start_path = stored_link if stored_link else drive.graph_path

    items_seen = 0
    final_delta: str | None = stored_link
    for items, delta_link in graph.get_paginated(start_path):
        for item in items:
            normalized = normalize_item(drive.drive_id, item)
            conn.execute(_UPSERT_ITEM_SQL, normalized)
            items_seen += 1
        if delta_link:
            final_delta = delta_link

    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO drives (drive_id, display_name, owner, drive_type,
                            delta_link, last_refreshed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (drive_id) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            owner = EXCLUDED.owner,
            drive_type = EXCLUDED.drive_type,
            delta_link = EXCLUDED.delta_link,
            last_refreshed_at = EXCLUDED.last_refreshed_at
        """,
        [drive.drive_id, drive.display_name, drive.owner, drive.drive_type,
         final_delta, now],
    )
    return CrawlResult(drive_id=drive.drive_id,
                       items_seen=items_seen,
                       delta_link=final_delta)
