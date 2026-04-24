"""Tests for m365ctl.mail.mutate.delete — soft delete via move-to-Deleted-Items."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.delete import execute_soft_delete


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_soft_delete_moves_to_deleteditems_well_known(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "m1", "parentFolderId": "deleteditems-id"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-del",
        action="mail.delete.soft",
        drive_id="me",
        item_id="m1",
        args={},
    )
    result = execute_soft_delete(
        op, graph, logger,
        before={"parent_folder_id": "inbox", "parent_folder_path": "/Inbox"},
    )
    assert result.status == "ok"
    assert result.after == {"parent_folder_id": "deleteditems-id",
                            "deleted_from": "inbox"}
    assert graph.post.call_args.args[0] == "/me/messages/m1/move"
    assert graph.post.call_args.kwargs["json"] == {"destinationId": "deleteditems"}
    entries = list(iter_audit_entries(logger))
    assert [e["phase"] for e in entries] == ["start", "end"]
    assert entries[0]["cmd"] == "mail-delete-soft"
    assert entries[0]["before"]["parent_folder_id"] == "inbox"
    assert entries[1]["after"]["parent_folder_id"] == "deleteditems-id"


def test_soft_delete_app_only_routes_via_users_upn(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "m1", "parentFolderId": "deleteditems-id"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-ao",
        action="mail.delete.soft",
        drive_id="bob@example.com",
        item_id="m1",
        args={"auth_mode": "app-only"},
    )
    execute_soft_delete(op, graph, logger, before={})
    assert graph.post.call_args.args[0] == "/users/bob@example.com/messages/m1/move"


def test_soft_delete_graph_error(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.post.side_effect = GraphError("not found")
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-err",
        action="mail.delete.soft",
        drive_id="me", item_id="m1",
        args={},
    )
    result = execute_soft_delete(op, graph, logger, before={})
    assert result.status == "error"
    assert "not found" in (result.error or "")


def test_soft_delete_captures_empty_before_gracefully(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "m1", "parentFolderId": "deleteditems-id"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-empty",
        action="mail.delete.soft",
        drive_id="me", item_id="m1",
        args={},
    )
    result = execute_soft_delete(op, graph, logger, before={})
    assert result.status == "ok"
    assert result.after == {"parent_folder_id": "deleteditems-id", "deleted_from": ""}
