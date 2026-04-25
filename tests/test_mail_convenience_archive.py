"""Tests for the archive convenience plan builder."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from m365ctl.common.planfile import PLAN_SCHEMA_VERSION
from m365ctl.mail.convenience.archive import build_archive_plan


def _row(
    *,
    message_id: str,
    folder: str = "Inbox",
    received_at: datetime,
    subject: str = "subj",
    from_address: str = "alice@example.com",
) -> dict:
    return {
        "message_id": message_id,
        "subject": subject,
        "from_address": from_address,
        "received_at": received_at,
        "parent_folder_path": folder,
    }


def test_build_archive_plan_emits_one_move_op_per_qualifying_message() -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    rows = [
        _row(message_id="m1", received_at=now - timedelta(days=120)),
        _row(message_id="m2", received_at=now - timedelta(days=200)),
        # Newer than cutoff (90d) → excluded.
        _row(message_id="m3", received_at=now - timedelta(days=10)),
        # Different folder → excluded.
        _row(message_id="m4", folder="Other",
             received_at=now - timedelta(days=400)),
    ]
    plan = build_archive_plan(
        rows,
        older_than_days=90,
        folder="Inbox",
        mailbox_upn="me",
        source_cmd="mail archive --older-than 90d --folder Inbox",
        scope="me",
        now=now,
    )
    ids = [op.item_id for op in plan.operations]
    assert ids == ["m1", "m2"]
    for op in plan.operations:
        assert op.action == "mail.move"
        assert op.drive_id == "me"
        # to_folder format: Archive/<YYYY>/<MM>
        recv = next(r for r in rows if r["message_id"] == op.item_id)["received_at"]
        assert op.args["to_folder"] == f"Archive/{recv:%Y}/{recv:%m}"


def test_build_archive_plan_rule_name_matches_yyyymm() -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    received = datetime(2025, 11, 3, tzinfo=timezone.utc)
    rows = [_row(message_id="m1", received_at=received)]
    plan = build_archive_plan(
        rows,
        older_than_days=30,
        folder="Inbox",
        mailbox_upn="me",
        source_cmd="cmd",
        scope="me",
        now=now,
    )
    assert plan.operations[0].args["rule_name"] == "mail-archive-202511"


def test_build_archive_plan_metadata() -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    plan = build_archive_plan(
        [],
        older_than_days=90,
        folder="Inbox",
        mailbox_upn="me",
        source_cmd="mail archive --older-than 90d --folder Inbox",
        scope="me",
        now=now,
    )
    assert plan.version == PLAN_SCHEMA_VERSION
    assert plan.source_cmd == "mail archive --older-than 90d --folder Inbox"
    assert plan.scope == "me"
    assert plan.created_at == now.isoformat()


def test_build_archive_plan_empty_input_yields_empty_plan() -> None:
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    plan = build_archive_plan(
        [],
        older_than_days=90,
        folder="Inbox",
        mailbox_upn="me",
        source_cmd="cmd",
        scope="me",
        now=now,
    )
    assert plan.operations == []
