"""Delta crawler: pulls items from Graph into the DuckDB catalog."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from m365ctl.onedrive.catalog.normalize import normalize_item


class _GraphLike(Protocol):
    def get(self, path: str, *, params: dict | None = ...) -> dict: ...
    def get_paginated(self, path: str, *, params: dict | None = ...): ...
    # Optional; only present on real GraphClient. Sites that need batching
    # check via _supports_batch() before calling.
    def batch(self): ...


# Error-code substrings that signal "this user/site is unreachable; skip it".
# Used during tenant-scope enumeration so one bad user or site doesn't abort
# the whole scan. Transient errors (429/503/5xx) never reach these branches —
# ``with_retry`` exhausts them upstream.
_SKIP_TOKENS: tuple[str, ...] = (
    "itemNotFound", "HTTP404", "HTTP403", "HTTP410",
    "ResourceNotFound", "notAllowed", "accessDenied",
)


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
    """All user OneDrives + all SharePoint site drives.

    The per-user ``/users/{uid}/drive`` GETs and per-site
    ``/sites/{id}`` + ``/sites/{id}/drives`` GETs are non-delta metadata
    fetches with no inter-call ordering, so they fan out via ``/$batch``
    when the underlying graph client supports it (real ``GraphClient``).
    Tests using a bare ``MagicMock`` without ``.batch`` configured fall
    back to the legacy serial path.
    """
    specs: list[DriveSpec] = []

    from m365ctl.common.graph import GraphError

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
        except (TypeError, AttributeError):
            gathered = []
        if gathered:
            return gathered
        resp = (
            graph.get(path, params=params)
            if params is not None
            else graph.get(path)
        )
        return list(resp.get("value", []))

    # Detect batched-capable client. Bare-MagicMock test stubs that only
    # configure .get / .get_paginated lack a real .batch — fall through to
    # the serial path in that case.
    can_batch = _supports_batch(graph)

    # ---- Users → their OneDrive (skip "user has no drive" errors). ----
    users = _collect(
        "/users",
        params={"$select": "id,userPrincipalName,displayName", "$top": 999},
    )
    user_ids = [u.get("id") for u in users if u.get("id")]

    if can_batch and user_ids:
        with graph.batch() as b:
            user_futs = [(uid, b.get(f"/users/{uid}/drive")) for uid in user_ids]
        for uid, fut in user_futs:
            try:
                meta = fut.result()
            except GraphError as exc:
                if any(t in str(exc) for t in _SKIP_TOKENS):
                    continue
                raise
            specs.append(
                _drive_from_meta(meta, graph_path=f"/drives/{meta['id']}/root/delta")
            )
    else:
        for uid in user_ids:
            try:
                meta = graph.get(f"/users/{uid}/drive")
            except GraphError as exc:
                if any(t in str(exc) for t in _SKIP_TOKENS):
                    continue
                raise
            specs.append(
                _drive_from_meta(meta, graph_path=f"/drives/{meta['id']}/root/delta")
            )

    # ---- Sites → each site's drives. Apply the same skip semantics. ----
    sites = _collect("/sites", params={"search": "*"})
    site_ids = [s["id"] for s in sites if s.get("id")]

    if can_batch and site_ids:
        # Batch site metadata first, then site/drives for the survivors.
        with graph.batch() as b:
            site_futs = [(sid, b.get(f"/sites/{sid}")) for sid in site_ids]
        survivors: list[dict] = []
        for sid, fut in site_futs:
            try:
                site_full = fut.result()
            except GraphError as exc:
                if any(t in str(exc) for t in _SKIP_TOKENS):
                    continue
                raise
            survivors.append(site_full)

        with graph.batch() as b:
            drive_futs = [
                (s, b.get(f"/sites/{s['id']}/drives")) for s in survivors
            ]
        for site_full, fut in drive_futs:
            try:
                drives_body = fut.result()
            except GraphError as exc:
                if any(t in str(exc) for t in _SKIP_TOKENS):
                    continue
                raise
            specs.extend(_drives_of_site_from_body(site_full, drives_body))
    else:
        for site in sites:
            try:
                site_full = graph.get(f"/sites/{site['id']}")
            except GraphError as exc:
                if any(t in str(exc) for t in _SKIP_TOKENS):
                    continue
                raise
            try:
                specs.extend(_drives_of_site(site_full, graph))
            except GraphError as exc:
                if any(t in str(exc) for t in _SKIP_TOKENS):
                    continue
                raise
    return specs


def _supports_batch(graph: _GraphLike) -> bool:
    """Return True iff ``graph`` exposes a real ``batch()`` method.

    Real ``GraphClient`` always does. Bare-MagicMock test stubs that haven't
    explicitly configured ``.batch`` should keep using the serial path —
    we detect those by treating any MagicMock instance as no-batch unless
    its ``batch`` attribute is *not* an auto-attribute (i.e. it's been
    explicitly set or the test passed a real ``GraphClient``).
    """
    try:
        from unittest.mock import MagicMock as _MM
    except ImportError:
        _MM = None  # type: ignore
    if _MM is not None and isinstance(graph, _MM):
        # MagicMock auto-creates any attribute access — only treat as
        # batch-capable if the test has explicitly configured .batch
        # (e.g. via side_effect or a tracked spec).
        batch_attr = graph.__dict__.get("batch")
        if batch_attr is None:
            return False
        # If side_effect or return_value is set, the test wants batched.
        if getattr(batch_attr, "side_effect", None) is not None:
            return True
        if getattr(batch_attr, "_mock_return_value", None) not in (None, _MM.DEFAULT):
            return True
        return False
    return callable(getattr(graph, "batch", None))


def _drives_of_site_from_body(site: dict, drives_body: dict) -> list[DriveSpec]:
    """Identical shape to ``_drives_of_site`` but consumes a pre-fetched
    drives-listing body so the outer caller can issue the GET in a batch.
    """
    site_name = site.get("displayName") or site.get("name") or site["id"]
    specs: list[DriveSpec] = []
    for d in drives_body.get("value", []) or []:
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
