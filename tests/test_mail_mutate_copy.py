"""Tests for m365ctl.mail.mutate.copy."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.copy import execute_copy


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_copy_posts_and_records_new_message_id(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "m1-copy", "parentFolderId": "archive"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-copy",
        action="mail.copy",
        drive_id="me",
        item_id="m1",
        args={"destination_id": "archive", "destination_path": "/Archive"},
    )
    result = execute_copy(op, graph, logger, before={})
    assert result.status == "ok"
    assert result.after == {
        "new_message_id": "m1-copy",
        "destination_folder_id": "archive",
    }
    assert graph.post.call_args.args[0] == "/me/messages/m1/copy"
    assert graph.post.call_args.kwargs["json"] == {"destinationId": "archive"}


def test_copy_graph_error(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.post.side_effect = GraphError("quota exceeded")
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-err",
        action="mail.copy",
        drive_id="me", item_id="m1",
        args={"destination_id": "archive"},
    )
    result = execute_copy(op, graph, logger, before={})
    assert result.status == "error"
    assert "quota" in (result.error or "")
