"""Normalize a Graph driveItem JSON into a flat dict ready for DuckDB insert.

Graph delta returns items in two shapes:
- Full: has ``file`` or ``folder`` plus all the usual metadata.
- Deleted tombstone: has ``deleted`` and a bare minimum (id + parentReference).

Our normaliser handles both. Unknown/missing fields become ``None``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_PATH_PREFIX = "/drive/root:"


def _strip_path_prefix(p: str | None) -> str:
    if not p:
        return "/"
    if p.startswith(_PATH_PREFIX):
        p = p[len(_PATH_PREFIX):]
    return p or "/"


def _actor_identifier(actor_block: dict | None) -> str | None:
    """Return email if available, else displayName, else None."""
    if not actor_block:
        return None
    for key in ("user", "application", "device"):
        inner = actor_block.get(key)
        if not inner:
            continue
        return inner.get("email") or inner.get("displayName")
    return None


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    # Graph returns 'Z' suffix; datetime.fromisoformat accepts +00:00 in 3.11+
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def normalize_item(drive_id: str, item: dict) -> dict[str, Any]:
    is_deleted = "deleted" in item
    parent_ref = item.get("parentReference") or {}
    parent_path = _strip_path_prefix(parent_ref.get("path"))
    name = item.get("name", "")
    if parent_path == "/":
        full_path = f"/{name}" if name else "/"
    else:
        full_path = f"{parent_path}/{name}" if name else parent_path

    is_folder = "folder" in item
    file_block = item.get("file") or {}
    row: dict[str, Any] = {
        "drive_id": drive_id,
        "item_id": item["id"],
        "name": name,
        "parent_path": parent_path,
        "full_path": full_path,
        "size": None if is_folder else item.get("size"),
        "mime_type": None if is_folder else file_block.get("mimeType"),
        "is_folder": is_folder,
        "is_deleted": is_deleted,
        "created_at": _parse_ts(item.get("createdDateTime")),
        "modified_at": _parse_ts(item.get("lastModifiedDateTime")),
        "created_by": _actor_identifier(item.get("createdBy")),
        "modified_by": _actor_identifier(item.get("lastModifiedBy")),
        "has_sharing": bool(item.get("shared")),
        "quick_xor_hash": (file_block.get("hashes") or {}).get("quickXorHash"),
        "etag": item.get("eTag"),
        "last_seen_at": datetime.now(timezone.utc),
    }
    return row
