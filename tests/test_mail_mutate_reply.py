from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.reply import (
    execute_create_reply,
    execute_create_reply_all,
    execute_send_reply_inline,
)


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_create_reply_posts_createReply(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "reply-1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-r", action="mail.reply",
        drive_id="me", item_id="m1", args={"mode": "create"},
    )
    result = execute_create_reply(op, graph, logger, before={})
    assert result.status == "ok"
    assert result.after == {"draft_id": "reply-1"}
    assert graph.post.call_args.args[0] == "/me/messages/m1/createReply"
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-reply(create)"


def test_create_reply_all_posts_createReplyAll(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "reply-all-1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-ra", action="mail.reply.all",
        drive_id="me", item_id="m1", args={"mode": "create"},
    )
    result = execute_create_reply_all(op, graph, logger, before={})
    assert result.status == "ok"
    assert result.after == {"draft_id": "reply-all-1"}
    assert graph.post.call_args.args[0] == "/me/messages/m1/createReplyAll"
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-reply.all(create)"


def test_send_reply_inline_posts_reply_endpoint_with_comment(tmp_path):
    graph = MagicMock()
    graph.post_raw.return_value = MagicMock(status_code=202, headers={})
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-i", action="mail.reply",
        drive_id="me", item_id="m1",
        args={"mode": "inline", "body": "ok"},
    )
    result = execute_send_reply_inline(op, graph, logger, before={})
    assert result.status == "ok"
    assert "sent_at" in (result.after or {})
    assert graph.post_raw.call_args.args[0] == "/me/messages/m1/reply"
    body = graph.post_raw.call_args.kwargs["json_body"]
    assert body == {"comment": "ok"}
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-reply(inline)"
