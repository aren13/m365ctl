"""Tests for m365ctl.mail.cli._bulk — pattern expansion + plan I/O."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.planfile import PLAN_SCHEMA_VERSION, Plan, load_plan
from m365ctl.mail.cli._bulk import (
    MessageFilter,
    emit_plan,
    expand_messages_for_pattern,
)
from m365ctl.mail.models import EmailAddress, Flag, Message
from datetime import datetime, timezone


def _msg(msg_id: str, folder_path: str = "/Inbox", subject: str = "s") -> Message:
    return Message(
        id=msg_id, mailbox_upn="me", internet_message_id=f"<{msg_id}>",
        conversation_id="c", conversation_index=b"",
        parent_folder_id="folder-id", parent_folder_path=folder_path,
        subject=subject,
        sender=EmailAddress(name="", address=""),
        from_addr=EmailAddress(name="A", address="a@example.com"),
        to=[], cc=[], bcc=[], reply_to=[],
        received_at=datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc),
        sent_at=None, is_read=False, is_draft=False, has_attachments=False,
        importance="normal",
        flag=Flag(status="notFlagged"),
        categories=[], inference_classification="focused",
        body_preview="", body=None, web_link="", change_key="ck",
    )


def test_expand_messages_single_folder():
    def fake_list(*, graph, mailbox_spec, auth_mode, folder_id, parent_folder_path, filters, limit, page_size):
        return [_msg("m1", parent_folder_path), _msg("m2", parent_folder_path)]
    resolved_folders = [("inbox", "/Inbox")]
    msgs = list(expand_messages_for_pattern(
        graph=MagicMock(),
        mailbox_spec="me",
        auth_mode="delegated",
        resolved_folders=resolved_folders,
        filter=MessageFilter(from_address="a@example.com"),
        limit=50,
        _list_messages_impl=fake_list,
    ))
    assert [m.id for m in msgs] == ["m1", "m2"]


def test_expand_messages_multiple_folders():
    def fake_list(*, folder_id, parent_folder_path, **_kw):
        if folder_id == "inbox":
            return [_msg("m1", parent_folder_path)]
        return [_msg("m2", parent_folder_path)]
    resolved = [("inbox", "/Inbox"), ("archive", "/Archive")]
    msgs = list(expand_messages_for_pattern(
        graph=MagicMock(),
        mailbox_spec="me",
        auth_mode="delegated",
        resolved_folders=resolved,
        filter=MessageFilter(),
        limit=50,
        _list_messages_impl=fake_list,
    ))
    assert [m.id for m in msgs] == ["m1", "m2"]


def test_expand_messages_respects_limit_across_folders():
    def fake_list(*, folder_id, **_kw):
        return [_msg(f"{folder_id}-{i}") for i in range(10)]
    resolved = [("inbox", "/Inbox"), ("archive", "/Archive"), ("trash", "/Trash")]
    msgs = list(expand_messages_for_pattern(
        graph=MagicMock(),
        mailbox_spec="me",
        auth_mode="delegated",
        resolved_folders=resolved,
        filter=MessageFilter(),
        limit=15,
        _list_messages_impl=fake_list,
    ))
    assert len(msgs) == 15


def test_emit_plan_writes_json_with_schema_version(tmp_path):
    plan_path = tmp_path / "out.json"
    from m365ctl.common.planfile import Operation
    ops = [
        Operation(op_id="1", action="mail.move", drive_id="me", item_id="m1",
                  args={"destination_id": "archive"},
                  dry_run_result="would move m1 -> /Archive"),
    ]
    emit_plan(plan_path, source_cmd="mail move --plan-out", scope="me", operations=ops)
    plan = load_plan(plan_path)
    assert plan.version == PLAN_SCHEMA_VERSION
    assert plan.source_cmd == "mail move --plan-out"
    assert len(plan.operations) == 1
    assert plan.operations[0].action == "mail.move"


def test_message_filter_applies_locally():
    msgs = [
        _msg("m1", subject="Meeting minutes"),
        _msg("m2", subject="Lunch plans"),
    ]
    f = MessageFilter(subject_contains="meeting")
    out = [m for m in msgs if f.match(m)]
    assert len(out) == 1
    assert out[0].id == "m1"
