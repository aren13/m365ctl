"""Tests for the size-report convenience wrapper."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.convenience.size_report import build_size_report


def _seed(catalog_path: Path) -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    rows = [
        # Inbox: 3 messages, 30 + 70 + 50 = 150
        ("me", "m1", "Inbox", 30),
        ("me", "m2", "Inbox", 70),
        ("me", "m3", "Inbox", 50),
        # Archive: 2 messages, 1000 + 500 = 1500
        ("me", "m4", "Archive", 1000),
        ("me", "m5", "Archive", 500),
        # Sent: 1 message, 200
        ("me", "m6", "Sent Items", 200),
    ]
    with open_catalog(catalog_path) as conn:
        for upn, mid, folder, size in rows:
            conn.execute(
                "INSERT INTO mail_messages (mailbox_upn, message_id, "
                "parent_folder_path, size_estimate, received_at, is_read, "
                "is_deleted) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [upn, mid, folder, size, now, False, False],
            )


def test_size_report_orders_by_total_size_desc(tmp_path: Path) -> None:
    db = tmp_path / "mail.duckdb"
    _seed(db)
    rows = build_size_report(db, mailbox_upn="me")
    folders = [r["parent_folder_path"] for r in rows]
    assert folders == ["Archive", "Sent Items", "Inbox"]
    archive = next(r for r in rows if r["parent_folder_path"] == "Archive")
    assert archive["message_count"] == 2
    assert archive["total_size"] == 1500


def test_size_report_top_truncates(tmp_path: Path) -> None:
    db = tmp_path / "mail.duckdb"
    _seed(db)
    rows = build_size_report(db, mailbox_upn="me", top=2)
    assert len(rows) == 2
    assert [r["parent_folder_path"] for r in rows] == ["Archive", "Sent Items"]


def test_size_report_missing_catalog_returns_empty(tmp_path: Path) -> None:
    db = tmp_path / "missing.duckdb"
    rows = build_size_report(db, mailbox_upn="me")
    assert rows == []
