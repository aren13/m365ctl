"""Normalize Graph mail JSON into flat dicts ready for DuckDB upsert.

Two shapes from ``/messages/delta``:
- Full message: standard payload.
- Deleted tombstone: ``{"id": "...", "@removed": {"reason": "deleted"}}``
  (Graph also uses a top-level ``@removed`` key for soft tombstones).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from m365ctl.mail.models import _parse_graph_datetime  # type: ignore[attr-defined]


def _addr(block: dict | None) -> tuple[str | None, str | None]:
    if not block:
        return None, None
    inner = block.get("emailAddress") or {}
    return inner.get("name") or None, inner.get("address") or None


def _join_addrs(items: list[dict] | None) -> str:
    if not items:
        return ""
    out: list[str] = []
    for it in items:
        inner = (it or {}).get("emailAddress") or {}
        addr = inner.get("address")
        if addr:
            out.append(addr)
    return ",".join(out)


def normalize_message(
    mailbox_upn: str, raw: dict, *, parent_folder_path: str
) -> dict[str, Any]:
    is_deleted = bool(raw.get("@removed"))
    if is_deleted:
        return {
            "mailbox_upn": mailbox_upn,
            "message_id": raw["id"],
            "internet_message_id": None,
            "conversation_id": None,
            "parent_folder_id": "",
            "parent_folder_path": "",
            "subject": None,
            "from_address": None,
            "from_name": None,
            "to_addresses": "",
            "received_at": None,
            "sent_at": None,
            "is_read": None,
            "is_draft": None,
            "has_attachments": None,
            "importance": None,
            "flag_status": None,
            "categories": "",
            "inference_class": None,
            "body_preview": None,
            "web_link": None,
            "size_estimate": None,
            "is_deleted": True,
            "last_seen_at": datetime.now(timezone.utc),
        }

    from_name, from_addr = _addr(raw.get("from"))
    flag = raw.get("flag") or {}
    return {
        "mailbox_upn": mailbox_upn,
        "message_id": raw["id"],
        "internet_message_id": raw.get("internetMessageId"),
        "conversation_id": raw.get("conversationId"),
        "parent_folder_id": raw.get("parentFolderId", ""),
        "parent_folder_path": parent_folder_path,
        "subject": raw.get("subject"),
        "from_address": from_addr,
        "from_name": from_name,
        "to_addresses": _join_addrs(raw.get("toRecipients")),
        "received_at": _parse_graph_datetime(raw.get("receivedDateTime")),
        "sent_at": _parse_graph_datetime(raw.get("sentDateTime")),
        "is_read": raw.get("isRead"),
        "is_draft": raw.get("isDraft"),
        "has_attachments": raw.get("hasAttachments"),
        "importance": raw.get("importance"),
        "flag_status": flag.get("flagStatus"),
        "categories": ",".join(raw.get("categories") or []),
        "inference_class": raw.get("inferenceClassification"),
        "body_preview": raw.get("bodyPreview"),
        "web_link": raw.get("webLink"),
        "size_estimate": None,
        "is_deleted": False,
        "last_seen_at": datetime.now(timezone.utc),
    }


def normalize_folder(
    mailbox_upn: str, raw: dict, *, path: str
) -> dict[str, Any]:
    return {
        "mailbox_upn": mailbox_upn,
        "folder_id": raw["id"],
        "display_name": raw.get("displayName"),
        "parent_folder_id": raw.get("parentFolderId"),
        "path": path,
        "well_known_name": raw.get("wellKnownName"),
        "total_items": raw.get("totalItemCount"),
        "unread_items": raw.get("unreadItemCount"),
        "child_folder_count": raw.get("childFolderCount"),
        "last_seen_at": datetime.now(timezone.utc),
    }
