from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.focus import execute_focus


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_focus_set_focused(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-f", action="mail.focus",
        drive_id="me", item_id="m1",
        args={"inference_classification": "focused"},
    )
    result = execute_focus(op, graph, logger, before={"inference_classification": "other"})
    assert result.status == "ok"
    assert result.after == {"inference_classification": "focused"}
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"inferenceClassification": "focused"}
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-focus"


def test_focus_set_other(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-o", action="mail.focus",
        drive_id="me", item_id="m1",
        args={"inference_classification": "other"},
    )
    execute_focus(op, graph, logger, before={"inference_classification": "focused"})
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"inferenceClassification": "other"}
