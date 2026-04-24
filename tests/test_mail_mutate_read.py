from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.read import execute_read


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_read_set_true(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-read", action="mail.read",
        drive_id="me", item_id="m1",
        args={"is_read": True},
    )
    result = execute_read(op, graph, logger, before={"is_read": False})
    assert result.status == "ok"
    assert result.after == {"is_read": True}
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"isRead": True}
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-read"
    assert entries[0]["before"]["is_read"] is False


def test_read_set_false(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-unread", action="mail.read",
        drive_id="me", item_id="m1",
        args={"is_read": False},
    )
    execute_read(op, graph, logger, before={"is_read": True})
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"isRead": False}
