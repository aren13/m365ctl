"""Tests for m365ctl.mail.mutate.draft."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.draft import (
    execute_create_draft,
    execute_delete_draft,
    execute_update_draft,
)


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_create_draft_posts_payload_and_records_new_id(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {
        "id": "draft-1", "subject": "Hello",
        "webLink": "https://outlook.office.com/?ItemID=d1",
    }
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-d", action="mail.draft.create",
        drive_id="me", item_id="",
        args={
            "subject": "Hello", "body": "Hi there", "body_type": "text",
            "to": ["alice@example.com"], "cc": [], "bcc": [],
        },
    )
    result = execute_create_draft(op, graph, logger, before={})
    assert result.status == "ok"
    assert result.after == {"id": "draft-1", "web_link": "https://outlook.office.com/?ItemID=d1"}
    assert graph.post.call_args.args[0] == "/me/messages"
    body = graph.post.call_args.kwargs["json"]
    assert body["subject"] == "Hello"
    assert body["toRecipients"] == [{"emailAddress": {"address": "alice@example.com"}}]
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-draft-create"


def test_update_draft_patches_fields(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "d1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-u", action="mail.draft.update",
        drive_id="me", item_id="d1",
        args={"subject": "Updated", "body": "new body"},
    )
    prior = {"subject": "Original", "body": {"contentType": "text", "content": "old"}}
    result = execute_update_draft(op, graph, logger, before=prior)
    assert result.status == "ok"
    assert graph.patch.call_args.args[0] == "/me/messages/d1"
    patch_body = graph.patch.call_args.kwargs["json_body"]
    assert patch_body["subject"] == "Updated"
    assert patch_body["body"] == {"contentType": "text", "content": "new body"}
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-draft-update"
    assert entries[0]["before"]["subject"] == "Original"


def test_update_draft_partial_only_sends_specified_fields(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "d1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-partial", action="mail.draft.update",
        drive_id="me", item_id="d1",
        args={"subject": "just subject"},
    )
    execute_update_draft(op, graph, logger, before={"subject": "old"})
    patch_body = graph.patch.call_args.kwargs["json_body"]
    assert patch_body == {"subject": "just subject"}


def test_delete_draft_captures_full_content_before_delete(tmp_path):
    graph = MagicMock()
    graph.delete.return_value = None
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-del", action="mail.draft.delete",
        drive_id="me", item_id="d1", args={},
    )
    prior = {
        "subject": "Draft subject",
        "body": {"contentType": "text", "content": "body text"},
        "toRecipients": [{"emailAddress": {"address": "alice@example.com"}}],
        "ccRecipients": [], "bccRecipients": [],
    }
    result = execute_delete_draft(op, graph, logger, before=prior)
    assert result.status == "ok"
    assert graph.delete.call_args.args[0] == "/me/messages/d1"
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-draft-delete"
    assert entries[0]["before"]["subject"] == "Draft subject"


def test_create_draft_graph_error(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.post.side_effect = GraphError("conflict")
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-err", action="mail.draft.create",
        drive_id="me", item_id="",
        args={"subject": "x", "body": "y", "to": ["a@example.com"]},
    )
    result = execute_create_draft(op, graph, logger, before={})
    assert result.status == "error"
    assert "conflict" in (result.error or "")


def test_create_draft_app_only_routes_via_users_upn(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "d2", "webLink": "x"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-ao", action="mail.draft.create",
        drive_id="bob@example.com", item_id="",
        args={
            "subject": "hi", "body": "y", "to": ["a@example.com"],
            "auth_mode": "app-only",
        },
    )
    execute_create_draft(op, graph, logger, before={})
    assert graph.post.call_args.args[0] == "/users/bob@example.com/messages"
