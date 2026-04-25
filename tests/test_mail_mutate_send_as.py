"""Tests for m365ctl.mail.mutate.send.execute_send_as (Phase 13)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.graph import GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.send import execute_send_as


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def _op(**overrides):
    args = {
        "from_upn": "bob@example.com",
        "subject": "hello",
        "body": "body text",
        "body_type": "text",
        "to": ["alice@example.com"],
        "authenticated_principal": "11111111-1111-1111-1111-111111111111",
    }
    args.update(overrides.pop("args", {}))
    return Operation(
        op_id=overrides.pop("op_id", "op-as"),
        action="mail.send.as",
        drive_id=overrides.pop("drive_id", "bob@example.com"),
        item_id=overrides.pop("item_id", ""),
        args=args,
    )


def test_send_as_posts_to_users_upn_sendmail(tmp_path: Path) -> None:
    graph = MagicMock()
    graph.post_raw.return_value = MagicMock(status_code=202, headers={})
    logger = _logger(tmp_path)
    op = _op()
    # Even with a stray auth_mode in args, app-only routing is forced.
    op.args["auth_mode"] = "delegated"
    result = execute_send_as(op, graph, logger, before={})
    assert result.status == "ok"
    assert graph.post_raw.call_args.args[0] == "/users/bob@example.com/sendMail"


def test_send_as_payload_has_message_and_save_to_sent(tmp_path: Path) -> None:
    graph = MagicMock()
    graph.post_raw.return_value = MagicMock(status_code=202, headers={})
    logger = _logger(tmp_path)
    op = _op()
    execute_send_as(op, graph, logger, before={})
    payload = graph.post_raw.call_args.kwargs["json_body"]
    assert payload["saveToSentItems"] is True
    assert payload["message"]["subject"] == "hello"


def test_send_as_after_records_dual_audit(tmp_path: Path) -> None:
    graph = MagicMock()
    graph.post_raw.return_value = MagicMock(status_code=202, headers={})
    logger = _logger(tmp_path)
    op = _op()
    result = execute_send_as(op, graph, logger, before={})
    assert result.status == "ok"
    after = result.after or {}
    assert after["effective_sender"] == "bob@example.com"
    assert after["authenticated_principal"] == (
        "11111111-1111-1111-1111-111111111111"
    )
    assert "sent_at" in after
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-sendas"


def test_send_as_body_format_error_returns_error_without_post(
    tmp_path: Path,
) -> None:
    graph = MagicMock()
    logger = _logger(tmp_path)
    op = _op(args={"subject": ""})  # empty subject triggers BodyFormatError
    result = execute_send_as(op, graph, logger, before={})
    assert result.status == "error"
    assert "subject" in (result.error or "").lower()
    graph.post_raw.assert_not_called()


def test_send_as_graph_error_returns_error(tmp_path: Path) -> None:
    graph = MagicMock()
    graph.post_raw.side_effect = GraphError("mailbox quota exceeded")
    logger = _logger(tmp_path)
    op = _op()
    result = execute_send_as(op, graph, logger, before={})
    assert result.status == "error"
    assert "quota" in (result.error or "")


def test_send_as_routes_recipients_through_build_message_payload(
    tmp_path: Path,
) -> None:
    graph = MagicMock()
    graph.post_raw.return_value = MagicMock(status_code=202, headers={})
    logger = _logger(tmp_path)
    op = _op(args={
        "to": ["alice@example.com", "carol@example.com"],
        "cc": ["dan@example.com"],
        "bcc": ["eve@example.com"],
    })
    execute_send_as(op, graph, logger, before={})
    msg = graph.post_raw.call_args.kwargs["json_body"]["message"]
    assert msg["toRecipients"] == [
        {"emailAddress": {"address": "alice@example.com"}},
        {"emailAddress": {"address": "carol@example.com"}},
    ]
    assert msg["ccRecipients"] == [
        {"emailAddress": {"address": "dan@example.com"}},
    ]
    assert msg["bccRecipients"] == [
        {"emailAddress": {"address": "eve@example.com"}},
    ]
