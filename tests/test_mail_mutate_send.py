"""Tests for m365ctl.mail.mutate.send."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.send import execute_send_draft, execute_send_new


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_send_draft_posts_send_endpoint(tmp_path):
    graph = MagicMock()
    graph.post_raw.return_value = MagicMock(status_code=202, headers={})
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-s", action="mail.send",
        drive_id="me", item_id="d1", args={},
    )
    result = execute_send_draft(op, graph, logger, before={})
    assert result.status == "ok"
    assert "sent_at" in (result.after or {})
    assert graph.post_raw.call_args.args[0] == "/me/messages/d1/send"
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-send"


def test_send_draft_graph_error(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.post_raw.side_effect = GraphError("mailbox quota exceeded")
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-err", action="mail.send",
        drive_id="me", item_id="d1", args={},
    )
    result = execute_send_draft(op, graph, logger, before={})
    assert result.status == "error"
    assert "quota" in (result.error or "")


def test_send_new_posts_sendMail_with_wrapped_payload(tmp_path):
    graph = MagicMock()
    graph.post_raw.return_value = MagicMock(status_code=202, headers={})
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-n", action="mail.send",
        drive_id="me", item_id="",
        args={
            "subject": "Hello", "body": "Body text",
            "to": ["alice@example.com"], "new": True,
        },
    )
    result = execute_send_new(op, graph, logger, before={})
    assert result.status == "ok"
    assert "sent_at" in (result.after or {})
    assert graph.post_raw.call_args.args[0] == "/me/sendMail"
    payload = graph.post_raw.call_args.kwargs["json_body"]
    assert payload["saveToSentItems"] is True
    assert payload["message"]["subject"] == "Hello"
    assert payload["message"]["toRecipients"] == [{"emailAddress": {"address": "alice@example.com"}}]


def test_send_new_rejects_empty_subject(tmp_path):
    graph = MagicMock()
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-blank", action="mail.send",
        drive_id="me", item_id="",
        args={"subject": "", "body": "body", "to": ["a@example.com"], "new": True},
    )
    result = execute_send_new(op, graph, logger, before={})
    assert result.status == "error"
    assert "subject" in (result.error or "").lower()


def test_send_new_app_only_routes_via_users_upn(tmp_path):
    graph = MagicMock()
    graph.post_raw.return_value = MagicMock(status_code=202, headers={})
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-ao", action="mail.send",
        drive_id="bob@example.com", item_id="",
        args={
            "subject": "x", "body": "y", "to": ["a@example.com"],
            "new": True, "auth_mode": "app-only",
        },
    )
    execute_send_new(op, graph, logger, before={})
    assert graph.post_raw.call_args.args[0] == "/users/bob@example.com/sendMail"
