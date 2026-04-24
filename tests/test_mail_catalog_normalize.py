from __future__ import annotations

from datetime import datetime

from m365ctl.mail.catalog.normalize import normalize_folder, normalize_message


def test_normalize_message_full_payload() -> None:
    raw = {
        "id": "msg-1",
        "internetMessageId": "<abc@example.com>",
        "conversationId": "conv-1",
        "parentFolderId": "fld-inbox",
        "subject": "Hello",
        "from": {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
        "toRecipients": [
            {"emailAddress": {"name": "Bob", "address": "bob@example.com"}},
            {"emailAddress": {"name": "Carol", "address": "carol@example.com"}},
        ],
        "receivedDateTime": "2026-04-01T10:00:00Z",
        "sentDateTime": "2026-04-01T09:59:59Z",
        "isRead": False,
        "isDraft": False,
        "hasAttachments": True,
        "importance": "high",
        "flag": {"flagStatus": "flagged"},
        "categories": ["Work", "Urgent"],
        "inferenceClassification": "focused",
        "bodyPreview": "preview text",
        "webLink": "https://outlook.office.com/...",
    }
    row = normalize_message("me", raw, parent_folder_path="Inbox")
    assert row["mailbox_upn"] == "me"
    assert row["message_id"] == "msg-1"
    assert row["from_address"] == "alice@example.com"
    assert row["from_name"] == "Alice"
    assert row["to_addresses"] == "bob@example.com,carol@example.com"
    assert row["categories"] == "Work,Urgent"
    assert row["is_read"] is False
    assert row["has_attachments"] is True
    assert row["parent_folder_path"] == "Inbox"
    assert row["is_deleted"] is False
    assert isinstance(row["last_seen_at"], datetime)


def test_normalize_message_deleted_tombstone() -> None:
    """Graph delta returns ``{"id": "...", "@removed": {"reason": "deleted"}}``."""
    raw = {"id": "msg-2", "@removed": {"reason": "deleted"}}
    row = normalize_message("me", raw, parent_folder_path="")
    assert row["message_id"] == "msg-2"
    assert row["is_deleted"] is True
    # Tombstones have minimal data; everything else is None / defaults.
    assert row["subject"] is None
    assert row["received_at"] is None


def test_normalize_message_handles_missing_from() -> None:
    raw = {
        "id": "msg-3",
        "parentFolderId": "fld-drafts",
        "subject": "Draft",
        "receivedDateTime": "2026-04-01T10:00:00Z",
    }
    row = normalize_message("me", raw, parent_folder_path="Drafts")
    assert row["from_address"] is None
    assert row["from_name"] is None
    assert row["to_addresses"] == ""


def test_normalize_folder() -> None:
    raw = {
        "id": "fld-inbox",
        "displayName": "Inbox",
        "parentFolderId": "fld-root",
        "wellKnownName": "inbox",
        "totalItemCount": 100,
        "unreadItemCount": 7,
        "childFolderCount": 2,
    }
    row = normalize_folder("me", raw, path="Inbox")
    assert row["folder_id"] == "fld-inbox"
    assert row["display_name"] == "Inbox"
    assert row["unread_items"] == 7
    assert row["well_known_name"] == "inbox"
