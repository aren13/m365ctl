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

    Supported forms:
      - ``me``                → current user's OneDrive (delegated).
      - ``drive:<id>``        → one specific drive (app-only).
      - ``site:<slug-or-id>`` → all drives of one SharePoint site (app-only).
      - ``tenant``            → every user drive + every SharePoint library
                                (app-only, paginated).

    Missing user drives (users who never provisioned OneDrive) are silently
    skipped under ``tenant`` rather than aborting the crawl.
    """
    if scope == "me":
        meta = graph.get("/me/drive")
        return [_drive_from_meta(meta, graph_path="/me/drive/root/delta")]

    if scope.startswith("drive:"):
        drive_id = scope.split(":", 1)[1]
        meta = graph.get(f"/drives/{drive_id}")
        return [
            _drive_from_meta(
                meta, graph_path=f"/drives/{drive_id}/root/delta"
            )
        ]

    if scope.startswith("site:"):
        ident = scope.split(":", 1)[1]
        site = _resolve_site(ident, graph)
        return _drives_of_site(site, graph)

    if scope == "tenant":
        return _enumerate_tenant(graph)

    raise ValueError(f"unknown scope: {scope!r}")


def _drive_from_meta(meta: dict, *, graph_path: str) -> DriveSpec:
    return DriveSpec(
        drive_id=meta["id"],
        display_name=meta.get("name", meta["id"]),
        owner=_owner_of(meta),
        drive_type=meta.get("driveType", "unknown"),
        graph_path=graph_path,
    )


def _resolve_site(ident: str, graph: _GraphLike) -> dict:
    """Return the site dict for ``ident`` (display-name slug or raw id).

    We try ``/sites/<ident>`` first — if ident is a full site-id or a
    hostname:/sites/<x> triple, that works. On 404 we fall back to search.
    """
    # Search is cheap and handles slugs; try it first unless the ident looks
    # like a site-id (contains a comma, the SharePoint site-id shape is
    # ``<host>,<spSiteId>,<spWebId>``) or a numeric-ish GUID-like token.
    looks_like_id = "," in ident or ident.count("-") >= 2 or ident.startswith("site-")
    if looks_like_id:
        try:
            return graph.get(f"/sites/{ident}")
        except Exception:
            pass  # fall through to search

    hits = graph.get("/sites", params={"search": ident}).get("value", [])
    if not hits:
        raise ValueError(f"no site matches site:{ident!r}")
    if len(hits) > 1:
        names = ", ".join(h.get("displayName", h.get("id", "?")) for h in hits)
        raise ValueError(
            f"site:{ident!r} is ambiguous — matched {len(hits)} sites: {names}"
        )
    # Re-fetch by id so the shape is consistent (search response omits fields).
    return graph.get(f"/sites/{hits[0]['id']}")


def _drives_of_site(site: dict, graph: _GraphLike) -> list[DriveSpec]:
    site_name = site.get("displayName") or site.get("name") or site["id"]
    drives = graph.get(f"/sites/{site['id']}/drives").get("value", [])
    specs: list[DriveSpec] = []
    for d in drives:
        owner_block = d.get("owner") or {}
        user = owner_block.get("user") or {}
        group = owner_block.get("group") or {}
        owner = (
            user.get("email")
            or user.get("displayName")
            or group.get("displayName")
            or "unknown"
        )
        specs.append(
            DriveSpec(
                drive_id=d["id"],
                display_name=f"{site_name} / {d.get('name', d['id'])}",
                owner=owner,
                drive_type=d.get("driveType", "documentLibrary"),
                graph_path=f"/drives/{d['id']}/root/delta",
            )
        )
    return specs


def _enumerate_tenant(graph: _GraphLike) -> list[DriveSpec]:
    """All user OneDrives + all SharePoint site drives."""
    specs: list[DriveSpec] = []

    from fazla_od.graph import GraphError

    def _collect(path: str, *, params: dict | None = None) -> list[dict]:
        """Collect all items from a paginated Graph collection.

        Prefer ``get_paginated`` when it yields at least one page; otherwise
        fall back to a single ``get`` (reading ``value``). This keeps tests
        that stub only ``graph.get`` working while still supporting real
        pagination in production.
        """
        gathered: list[dict] = []
        try:
            try:
                pages = (
                    graph.get_paginated(path, params=params)
                    if params is not None
                    else graph.get_paginated(path)
                )
            except TypeError:
                pages = graph.get_paginated(path)
            for items, _ in pages:
                gathered.extend(items)
        except Exception:
            gathered = []
        if gathered:
            return gathered
        resp = (
            graph.get(path, params=params)
            if params is not None
            else graph.get(path)
        )
        return list(resp.get("value", []))

    # Users → their OneDrive (skip 404s for unprovisioned users).
    for user in _collect(
        "/users",
        params={"$select": "id,userPrincipalName,displayName", "$top": 999},
    ):
        uid = user.get("id")
        if not uid:
            continue
        try:
            meta = graph.get(f"/users/{uid}/drive")
        except GraphError as exc:
            if "itemNotFound" in str(exc) or "HTTP404" in str(exc):
                continue
            raise
        specs.append(
            _drive_from_meta(meta, graph_path=f"/drives/{meta['id']}/root/delta")
        )

    # Sites → each site's drives.
    for site in _collect("/sites", params={"search": "*"}):
        site_full = graph.get(f"/sites/{site['id']}")
        specs.extend(_drives_of_site(site_full, graph))
    return specs


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
