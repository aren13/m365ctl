"""Tests for m365ctl.mail.convenience.top_senders."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


def _seed(catalog_path: Path) -> datetime:
    from m365ctl.mail.catalog.db import open_catalog
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    rows = [
        # (message_id, from_address, days_ago)
        ("m1", "alice@example.com", 0),
        ("m2", "alice@example.com", 1),
        ("m3", "alice@example.com", 40),
        ("m4", "bob@example.com", 0),
        ("m5", "bob@example.com", 10),
        ("m6", "carol@example.com", 100),
    ]
    with open_catalog(catalog_path) as conn:
        for mid, addr, days in rows:
            conn.execute(
                "INSERT INTO mail_messages (mailbox_upn, message_id, "
                "from_address, received_at, is_read, is_deleted) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ["me", mid, addr, now - timedelta(days=days), False, False],
            )
    return now


def test_top_senders_without_since_returns_all(tmp_path: Path) -> None:
    from m365ctl.mail.catalog.db import open_catalog
    from m365ctl.mail.convenience.top_senders import top_senders_since
    catalog = tmp_path / "mail.duckdb"
    _seed(catalog)
    with open_catalog(catalog) as conn:
        rows = top_senders_since(conn, mailbox_upn="me", since=None)
    # Counts: alice=3, bob=2, carol=1
    assert rows[0]["from_address"] == "alice@example.com"
    assert rows[0]["count"] == 3
    assert rows[1]["from_address"] == "bob@example.com"
    assert rows[1]["count"] == 2
    assert rows[2]["from_address"] == "carol@example.com"
    assert rows[2]["count"] == 1


def test_top_senders_since_filters_by_received_at(tmp_path: Path) -> None:
    from m365ctl.mail.catalog.db import open_catalog
    from m365ctl.mail.convenience.top_senders import top_senders_since
    catalog = tmp_path / "mail.duckdb"
    now = _seed(catalog)
    since = now - timedelta(days=30)
    with open_catalog(catalog) as conn:
        rows = top_senders_since(
            conn, mailbox_upn="me", since=since,
        )
    # alice=2 (m1,m2), bob=2 (m4,m5); carol & alice's m3 excluded
    addrs = {r["from_address"]: r["count"] for r in rows}
    assert addrs == {"alice@example.com": 2, "bob@example.com": 2}


def test_top_senders_limit_truncates(tmp_path: Path) -> None:
    from m365ctl.mail.catalog.db import open_catalog
    from m365ctl.mail.convenience.top_senders import top_senders_since
    catalog = tmp_path / "mail.duckdb"
    _seed(catalog)
    with open_catalog(catalog) as conn:
        rows = top_senders_since(conn, mailbox_upn="me", since=None, limit=2)
    assert len(rows) == 2
    assert [r["from_address"] for r in rows] == [
        "alice@example.com", "bob@example.com",
    ]
