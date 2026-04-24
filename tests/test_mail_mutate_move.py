"""Tests for m365ctl.mail.mutate.move."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.move import execute_move


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_move_posts_to_message_move_with_destination(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "m1", "parentFolderId": "archive"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-mv",
        action="mail.move",
        drive_id="me",
        item_id="m1",
        args={"destination_id": "archive", "destination_path": "/Archive"},
    )
    result = execute_move(
        op, graph, logger,
        before={"parent_folder_id": "inbox", "parent_folder_path": "/Inbox"},
    )
    assert result.status == "ok"
    assert result.after == {"parent_folder_id": "archive"}
    assert graph.post.call_args.args[0] == "/me/messages/m1/move"
    assert graph.post.call_args.kwargs["json"] == {"destinationId": "archive"}
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-move"
    assert entries[0]["before"]["parent_folder_id"] == "inbox"


def test_move_app_only_routes_via_users_upn(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "m1", "parentFolderId": "archive"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-ao",
        action="mail.move",
        drive_id="bob@example.com",
        item_id="m1",
        args={"destination_id": "archive", "destination_path": "/Archive",
              "auth_mode": "app-only"},
    )
    execute_move(op, graph, logger, before={})
    assert graph.post.call_args.args[0] == "/users/bob@example.com/messages/m1/move"


def test_move_graph_error(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.post.side_effect = GraphError("not found")
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-err",
        action="mail.move",
        drive_id="me", item_id="m1",
        args={"destination_id": "archive"},
    )
    result = execute_move(op, graph, logger, before={})
    assert result.status == "error"
    assert "not found" in (result.error or "")
