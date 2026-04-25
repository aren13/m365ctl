"""Tests for m365ctl.mail.convenience.digest — pure-logic builder + renderers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def test_parse_since_hours():
    from m365ctl.mail.convenience.digest import parse_since
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    got = parse_since("24h", now=now)
    assert got == now - timedelta(hours=24)


def test_parse_since_days():
    from m365ctl.mail.convenience.digest import parse_since
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    got = parse_since("3d", now=now)
    assert got == now - timedelta(days=3)


def test_parse_since_iso():
    from m365ctl.mail.convenience.digest import parse_since
    got = parse_since("2026-04-20T00:00:00Z")
    assert got == datetime(2026, 4, 20, 0, 0, tzinfo=timezone.utc)


def test_parse_since_garbage_raises():
    from m365ctl.mail.convenience.digest import DigestError, parse_since
    with pytest.raises(DigestError):
        parse_since("garbage")


def _fixture_rows(now: datetime) -> list[dict]:
    return [
        {
            "message_id": "m1",
            "subject": "Quarterly review",
            "from_address": "alice@example.com",
            "received_at": now - timedelta(hours=1),
            "categories": "Work,Triage",
        },
        {
            "message_id": "m2",
            "subject": "Lunch?",
            "from_address": "bob@example.com",
            "received_at": now - timedelta(hours=3),
            "categories": "",
        },
        {
            "message_id": "m3",
            "subject": "Re: Quarterly review",
            "from_address": "alice@example.com",
            "received_at": now - timedelta(hours=5),
            "categories": "Work",
        },
        {
            "message_id": "m4",
            "subject": "Old chatter",
            "from_address": "carol@example.com",
            # Outside the since cutoff (48h ago, since=24h).
            "received_at": now - timedelta(hours=48),
            "categories": "Work",
        },
        {
            "message_id": "m5",
            "subject": "Heads up",
            "from_address": "alice@example.com",
            "received_at": now - timedelta(hours=2),
            "categories": "Triage",
        },
    ]


def test_build_digest_populates_sections():
    from m365ctl.mail.convenience.digest import build_digest
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    since = now - timedelta(hours=24)
    rows = _fixture_rows(now)
    d = build_digest(rows, since=since, now=now, limit=20)
    assert d.total == 4  # m4 is filtered out
    # alice has 3, bob 1, carol 0 (filtered)
    senders = dict(d.top_senders)
    assert senders["alice@example.com"] == 3
    assert senders["bob@example.com"] == 1
    assert "carol@example.com" not in senders
    # Categories: Work=2 (m1+m3), Triage=2 (m1+m5), uncategorised=1 (m2)
    assert d.by_category["Work"] == 2
    assert d.by_category["Triage"] == 2
    assert d.by_category["(uncategorised)"] == 1
    # Recent ordered newest first.
    assert [e.message_id for e in d.recent] == ["m1", "m5", "m2", "m3"]
    assert d.recent[0].subject == "Quarterly review"
    assert d.recent[0].categories == ["Work", "Triage"]


def test_render_text_has_sections():
    from m365ctl.mail.convenience.digest import build_digest, render_text
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    since = now - timedelta(hours=24)
    d = build_digest(_fixture_rows(now), since=since, now=now)
    out = render_text(d)
    assert "Mail digest" in out
    assert "Total: 4 unread" in out
    assert "Top senders:" in out
    assert "alice@example.com" in out
    assert "By category:" in out
    assert "Recent (4)" in out
    assert "Quarterly review" in out


def test_render_html_has_h2_and_ul():
    from m365ctl.mail.convenience.digest import build_digest, render_html
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    since = now - timedelta(hours=24)
    d = build_digest(_fixture_rows(now), since=since, now=now)
    html = render_html(d)
    assert "<h2>" in html
    assert "<ul>" in html
    assert "<li>" in html
    assert "alice@example.com" in html
    assert "Quarterly review" in html
