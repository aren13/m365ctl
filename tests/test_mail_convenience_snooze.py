"""Tests for m365ctl.mail.convenience.snooze."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest


def test_parse_until_iso_date():
    from m365ctl.mail.convenience.snooze import parse_until
    assert parse_until("2026-05-01") == date(2026, 5, 1)


def test_parse_until_relative_days():
    from m365ctl.mail.convenience.snooze import parse_until
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    assert parse_until("5d", now=now) == (now + timedelta(days=5)).date()


def test_parse_until_relative_hours():
    from m365ctl.mail.convenience.snooze import parse_until
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    assert parse_until("24h", now=now) == (now + timedelta(hours=24)).date()


def test_parse_until_garbage_raises():
    from m365ctl.mail.convenience.snooze import SnoozeError, parse_until
    with pytest.raises(SnoozeError):
        parse_until("garbage")
    with pytest.raises(SnoozeError):
        parse_until("")


def test_build_snooze_ops_emits_move_and_categorize():
    from m365ctl.mail.convenience.snooze import build_snooze_ops
    ops = build_snooze_ops("MID-1", date(2026, 5, 1), "alice@example.com")
    assert len(ops) == 2
    move_op, cat_op = ops
    assert move_op.action == "mail.move"
    assert move_op.item_id == "MID-1"
    assert move_op.args["to_folder"] == "Deferred/2026-05-01"
    assert cat_op.action == "mail.categorize"
    assert cat_op.item_id == "MID-1"
    assert cat_op.args["add"] == ["Snooze/2026-05-01"]


def test_find_due_snoozed_filters_by_today():
    from m365ctl.mail.convenience.snooze import find_due_snoozed
    folders = [
        "Inbox",
        "Deferred/2026-04-20",   # past — due
        "Deferred/2026-04-25",   # today — due
        "Deferred/2026-05-01",   # future — not due
        "Deferred/not-a-date",   # bad — skipped
        "Archive",
    ]
    today = date(2026, 4, 25)
    got = find_due_snoozed(folders, today=today)
    assert got == [
        ("Deferred/2026-04-20", date(2026, 4, 20)),
        ("Deferred/2026-04-25", date(2026, 4, 25)),
    ]
