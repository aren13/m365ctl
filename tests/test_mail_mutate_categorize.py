from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.categorize import execute_categorize


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_categorize_sets_new_list(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-cat", action="mail.categorize",
        drive_id="me", item_id="m1",
        args={"categories": ["Followup", "Waiting"]},
    )
    result = execute_categorize(
        op, graph, logger,
        before={"categories": ["Archived"]},
    )
    assert result.status == "ok"
    assert result.after == {"categories": ["Followup", "Waiting"]}
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"categories": ["Followup", "Waiting"]}
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-categorize"
    assert entries[0]["before"]["categories"] == ["Archived"]


def test_categorize_clear_to_empty(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-clear", action="mail.categorize",
        drive_id="me", item_id="m1",
        args={"categories": []},
    )
    result = execute_categorize(op, graph, logger, before={"categories": ["X"]})
    assert result.status == "ok"
    assert result.after == {"categories": []}
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"categories": []}
