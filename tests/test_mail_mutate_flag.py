from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.flag import execute_flag


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_flag_set_flagged(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-flag", action="mail.flag",
        drive_id="me", item_id="m1",
        args={"status": "flagged", "due_at": "2026-04-30T17:00:00Z"},
    )
    result = execute_flag(
        op, graph, logger,
        before={"status": "notFlagged", "start_at": None, "due_at": None, "completed_at": None},
    )
    assert result.status == "ok"
    assert graph.patch.call_args.args[0] == "/me/messages/m1"
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {
        "flag": {"flagStatus": "flagged", "dueDateTime": {"dateTime": "2026-04-30T17:00:00Z", "timeZone": "UTC"}},
    }
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-flag"
    assert entries[0]["before"]["status"] == "notFlagged"


def test_flag_clear(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-clear", action="mail.flag",
        drive_id="me", item_id="m1",
        args={"status": "notFlagged"},
    )
    result = execute_flag(op, graph, logger, before={"status": "flagged"})
    assert result.status == "ok"
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"flag": {"flagStatus": "notFlagged"}}


def test_flag_with_etag(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-etag", action="mail.flag",
        drive_id="me", item_id="m1",
        args={"status": "flagged", "change_key": "ck-123"},
    )
    execute_flag(op, graph, logger, before={})
    headers = graph.patch.call_args.kwargs.get("headers")
    assert headers is not None
    assert headers.get("If-Match") == "ck-123"
