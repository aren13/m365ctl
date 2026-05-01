"""Tests for m365ctl.mail.cli._bulk — pattern expansion + plan I/O."""
from __future__ import annotations

from unittest.mock import MagicMock

from m365ctl.common.planfile import PLAN_SCHEMA_VERSION, load_plan
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


import json

import httpx

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation
from m365ctl.mail.cli._bulk import execute_plan_in_batches
from m365ctl.mail.mutate._common import MailResult


def _op(op_id: str) -> Operation:
    return Operation(
        op_id=op_id, action="mail.move", drive_id="me", item_id=op_id,
        args={"destination_id": "archive"},
    )


def test_execute_plan_in_batches_runs_phase1_then_phase2(tmp_path):
    """Phase 1 batches the `before` GETs; Phase 2 batches the mutations."""
    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        posts.append(body)
        return httpx.Response(200, json={
            "responses": [
                {"id": r["id"], "status": 200, "headers": {}, "body": {"id": "echo-" + r["id"]}}
                for r in body["requests"]
            ],
        })

    graph = GraphClient(
        token_provider=lambda: "tok",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    ops = [_op(f"op{i}") for i in range(3)]

    def fetch_before(b, op):
        return b.get(f"/me/messages/{op.item_id}")

    def parse_before(op, body, err):
        return {"parent_folder_id": "inbox"} if body else {}

    def start_op(op, b, logger, *, before):
        f = b.post(f"/me/messages/{op.item_id}/move", json={"destinationId": "archive"})
        return f, {"parent_folder_id": "archive"}

    def finish_op(op, future, after, logger):
        try:
            future.result()
        except Exception as e:
            return MailResult(op_id=op.op_id, status="error", error=str(e))
        return MailResult(op_id=op.op_id, status="ok", after=after)

    results: list[tuple[Operation, MailResult]] = []
    rc = execute_plan_in_batches(
        graph=graph, logger=logger, ops=ops,
        fetch_before=fetch_before, parse_before=parse_before,
        start_op=start_op, finish_op=finish_op,
        on_result=lambda op, r: results.append((op, r)),
    )
    assert rc == 0
    # Two /$batch POSTs: phase 1 (3 GETs), phase 2 (3 POSTs).
    assert len(posts) == 2
    assert all(r["method"] == "GET" for r in posts[0]["requests"])
    assert all(r["method"] == "POST" for r in posts[1]["requests"])
    assert [r.status for _, r in results] == ["ok", "ok", "ok"]


def test_execute_plan_in_batches_skips_phase1_when_fetch_before_is_none(tmp_path):
    """Verbs like mail.flag pass fetch_before=None and skip the GET pass."""
    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        posts.append(body)
        return httpx.Response(200, json={
            "responses": [
                {"id": r["id"], "status": 200, "headers": {}, "body": {}}
                for r in body["requests"]
            ],
        })

    graph = GraphClient(
        token_provider=lambda: "tok",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    ops = [_op("op1"), _op("op2")]

    def start_op(op, b, logger, *, before):
        f = b.patch(f"/me/messages/{op.item_id}", json_body={"isRead": True})
        return f, {"is_read": True}

    def finish_op(op, future, after, logger):
        try:
            future.result()
        except Exception as e:
            return MailResult(op_id=op.op_id, status="error", error=str(e))
        return MailResult(op_id=op.op_id, status="ok", after=after)

    rc = execute_plan_in_batches(
        graph=graph, logger=logger, ops=ops,
        fetch_before=None,
        parse_before=lambda op, body, err: {},
        start_op=start_op, finish_op=finish_op,
        on_result=lambda op, r: None,
    )
    assert rc == 0
    # Only one /$batch POST — phase 2 only.
    assert len(posts) == 1
    assert all(r["method"] == "PATCH" for r in posts[0]["requests"])


def test_execute_plan_in_batches_returns_1_when_any_op_errors(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        responses = []
        for i, r in enumerate(body["requests"]):
            if i == 0:
                responses.append({"id": r["id"], "status": 200, "headers": {}, "body": {}})
            else:
                responses.append({
                    "id": r["id"], "status": 404, "headers": {},
                    "body": {"error": {"code": "ItemNotFound", "message": "gone"}},
                })
        return httpx.Response(200, json={"responses": responses})

    graph = GraphClient(
        token_provider=lambda: "tok",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    ops = [_op("op1"), _op("op2")]

    def start_op(op, b, logger, *, before):
        f = b.delete(f"/me/messages/{op.item_id}")
        return f, {"deleted": True}

    def finish_op(op, future, after, logger):
        try:
            future.result()
        except Exception as e:
            return MailResult(op_id=op.op_id, status="error", error=str(e))
        return MailResult(op_id=op.op_id, status="ok", after=after)

    rc = execute_plan_in_batches(
        graph=graph, logger=logger, ops=ops,
        fetch_before=None,
        parse_before=lambda op, body, err: {},
        start_op=start_op, finish_op=finish_op,
        on_result=lambda op, r: None,
    )
    # One success, one error → rc=1.
    assert rc == 1
