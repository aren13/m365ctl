from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.forward import execute_create_forward, execute_send_forward_inline


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_create_forward_posts_createForward(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "fwd-1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-f", action="mail.forward",
        drive_id="me", item_id="m1", args={"mode": "create"},
    )
    result = execute_create_forward(op, graph, logger, before={})
    assert result.status == "ok"
    assert result.after == {"draft_id": "fwd-1"}
    assert graph.post.call_args.args[0] == "/me/messages/m1/createForward"
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-forward(create)"


def test_send_forward_inline_includes_to_recipients(tmp_path):
    graph = MagicMock()
    graph.post_raw.return_value = MagicMock(status_code=202, headers={})
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-fi", action="mail.forward",
        drive_id="me", item_id="m1",
        args={"mode": "inline", "body": "fyi", "to": ["carol@example.com"]},
    )
    result = execute_send_forward_inline(op, graph, logger, before={})
    assert result.status == "ok"
    assert graph.post_raw.call_args.args[0] == "/me/messages/m1/forward"
    body = graph.post_raw.call_args.kwargs["json_body"]
    assert body["comment"] == "fyi"
    assert body["toRecipients"] == [{"emailAddress": {"address": "carol@example.com"}}]
