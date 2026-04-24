from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.catalog.queries import (
    attachments_by_size,
    by_sender,
    older_than,
    size_per_folder,
    summary,
    top_senders,
    unread_in_folder,
)


def _seed(conn, **overrides) -> None:
    base = {
        "mailbox_upn": "me",
        "message_id": "m-x",
        "internet_message_id": None,
        "conversation_id": None,
        "parent_folder_id": "fld-inbox",
        "parent_folder_path": "Inbox",
        "subject": "subj",
        "from_address": "alice@example.com",
        "from_name": "Alice",
        "to_addresses": "",
        "received_at": datetime.now(timezone.utc) - timedelta(days=1),
        "sent_at": None,
        "is_read": False,
        "is_draft": False,
        "has_attachments": False,
        "importance": "normal",
        "flag_status": "notFlagged",
        "categories": "",
        "inference_class": "focused",
        "body_preview": "",
        "web_link": "",
        "size_estimate": 0,
        "is_deleted": False,
        "last_seen_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    conn.execute(
        "INSERT INTO mail_messages (mailbox_upn, message_id, internet_message_id, "
        "conversation_id, parent_folder_id, parent_folder_path, subject, "
        "from_address, from_name, to_addresses, received_at, sent_at, is_read, "
        "is_draft, has_attachments, importance, flag_status, categories, "
        "inference_class, body_preview, web_link, size_estimate, is_deleted, "
        "last_seen_at) VALUES ("
        "$mailbox_upn, $message_id, $internet_message_id, $conversation_id, "
        "$parent_folder_id, $parent_folder_path, $subject, $from_address, "
        "$from_name, $to_addresses, $received_at, $sent_at, $is_read, $is_draft, "
        "$has_attachments, $importance, $flag_status, $categories, "
        "$inference_class, $body_preview, $web_link, $size_estimate, "
        "$is_deleted, $last_seen_at)",
        base,
    )


def test_unread_in_folder(tmp_path: Path) -> None:
    with open_catalog(tmp_path / "m.duckdb") as conn:
        _seed(conn, message_id="m1", is_read=False)
        _seed(conn, message_id="m2", is_read=True)
        _seed(conn, message_id="m3", is_read=False, is_deleted=True)
        rows = unread_in_folder(conn, mailbox_upn="me", folder_id="fld-inbox")
    assert [r["message_id"] for r in rows] == ["m1"]


def test_older_than(tmp_path: Path) -> None:
    with open_catalog(tmp_path / "m.duckdb") as conn:
        _seed(conn, message_id="old", received_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
        _seed(conn, message_id="new", received_at=datetime(2026, 4, 1, tzinfo=timezone.utc))
        rows = older_than(conn, mailbox_upn="me", cutoff="2025-01-01")
    assert [r["message_id"] for r in rows] == ["old"]


def test_by_sender(tmp_path: Path) -> None:
    with open_catalog(tmp_path / "m.duckdb") as conn:
        _seed(conn, message_id="a", from_address="alice@example.com")
        _seed(conn, message_id="b", from_address="bob@example.com")
        rows = by_sender(conn, mailbox_upn="me", sender="alice@example.com")
    assert [r["message_id"] for r in rows] == ["a"]


def test_attachments_by_size(tmp_path: Path) -> None:
    with open_catalog(tmp_path / "m.duckdb") as conn:
        _seed(conn, message_id="big", has_attachments=True, size_estimate=5_000_000)
        _seed(conn, message_id="small", has_attachments=True, size_estimate=1_000)
        _seed(conn, message_id="none", has_attachments=False, size_estimate=0)
        rows = attachments_by_size(conn, mailbox_upn="me", min_bytes=10_000)
    assert [r["message_id"] for r in rows] == ["big"]


def test_top_senders(tmp_path: Path) -> None:
    with open_catalog(tmp_path / "m.duckdb") as conn:
        for i, addr in enumerate(["a@x.com", "a@x.com", "a@x.com", "b@x.com"]):
            _seed(conn, message_id=f"m{i}", from_address=addr)
        rows = top_senders(conn, mailbox_upn="me", limit=2)
    assert rows[0]["from_address"] == "a@x.com"
    assert rows[0]["count"] == 3
    assert rows[1]["from_address"] == "b@x.com"


def test_size_per_folder(tmp_path: Path) -> None:
    with open_catalog(tmp_path / "m.duckdb") as conn:
        _seed(conn, message_id="i1", parent_folder_path="Inbox", size_estimate=100)
        _seed(conn, message_id="i2", parent_folder_path="Inbox", size_estimate=200)
        _seed(conn, message_id="s1", parent_folder_path="Sent Items", size_estimate=50)
        rows = size_per_folder(conn, mailbox_upn="me")
    by_path = {r["parent_folder_path"]: r for r in rows}
    assert by_path["Inbox"]["total_size"] == 300
    assert by_path["Sent Items"]["total_size"] == 50


def test_summary(tmp_path: Path) -> None:
    with open_catalog(tmp_path / "m.duckdb") as conn:
        _seed(conn, message_id="m1")
        _seed(conn, message_id="m2", is_deleted=True)
        s = summary(conn, mailbox_upn="me")
    assert s["messages_total"] == 1
    assert s["messages_deleted"] == 1
    assert "last_refreshed_at" in s
