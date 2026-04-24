"""Tests for m365ctl.mail.mutate.folders — mocked Graph + AuditLogger."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.folders import (
    execute_create_folder,
    execute_delete_folder,
    execute_move_folder,
    execute_rename_folder,
)


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def _graph() -> MagicMock:
    return MagicMock()


# ---- create_folder ---------------------------------------------------------

def test_create_folder_posts_and_records_after(tmp_path):
    graph = _graph()
    graph.post.return_value = {"id": "new-folder-id", "displayName": "Triage"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-create-1",
        action="mail.folder.create",
        drive_id="me",
        item_id="inbox",
        args={"name": "Triage", "parent_path": "/Inbox"},
    )
    result = execute_create_folder(op, graph, logger, before={})
    assert result.status == "ok"
    assert result.after == {"id": "new-folder-id", "path": "/Inbox/Triage"}
    assert graph.post.call_args.args[0] == "/me/mailFolders/inbox/childFolders"
    assert graph.post.call_args.kwargs["json"] == {"displayName": "Triage"}

    entries = list(iter_audit_entries(logger))
    assert [e["phase"] for e in entries] == ["start", "end"]
    assert entries[0]["cmd"] == "mail-folder-create"
    assert entries[1]["after"] == {"id": "new-folder-id", "path": "/Inbox/Triage"}


def test_create_folder_root_level(tmp_path):
    graph = _graph()
    graph.post.return_value = {"id": "top", "displayName": "Archive"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-2",
        action="mail.folder.create",
        drive_id="me",
        item_id="",
        args={"name": "Archive", "parent_path": ""},
    )
    result = execute_create_folder(op, graph, logger, before={})
    assert result.status == "ok"
    assert graph.post.call_args.args[0] == "/me/mailFolders"


def test_create_folder_graph_error(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = _graph()
    graph.post.side_effect = GraphError("conflict: folder exists")
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-3",
        action="mail.folder.create",
        drive_id="me",
        item_id="inbox",
        args={"name": "Dup", "parent_path": "/Inbox"},
    )
    result = execute_create_folder(op, graph, logger, before={})
    assert result.status == "error"
    assert "conflict" in (result.error or "")


# ---- rename_folder ---------------------------------------------------------

def test_rename_folder_patches_and_records_before(tmp_path):
    graph = _graph()
    graph.patch.return_value = {"id": "f1", "displayName": "Triaged"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-rename",
        action="mail.folder.rename",
        drive_id="me",
        item_id="f1",
        args={"new_name": "Triaged"},
    )
    result = execute_rename_folder(op, graph, logger,
                                   before={"display_name": "Triage", "path": "/Inbox/Triage"})
    assert result.status == "ok"
    assert result.after == {"display_name": "Triaged"}
    assert graph.patch.call_args.args[0] == "/me/mailFolders/f1"
    assert graph.patch.call_args.kwargs["json_body"] == {"displayName": "Triaged"}

    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-folder-rename"
    assert entries[0]["before"] == {"display_name": "Triage", "path": "/Inbox/Triage"}


# ---- move_folder -----------------------------------------------------------

def test_move_folder_posts_move_and_records_before(tmp_path):
    graph = _graph()
    graph.post.return_value = {"id": "f1", "parentFolderId": "archive"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-move",
        action="mail.folder.move",
        drive_id="me",
        item_id="f1",
        args={"destination_id": "archive", "destination_path": "/Archive"},
    )
    result = execute_move_folder(op, graph, logger,
                                 before={"parent_id": "inbox", "path": "/Inbox/Triage"})
    assert result.status == "ok"
    assert result.after == {"parent_id": "archive", "path": "/Archive"}
    assert graph.post.call_args.args[0] == "/me/mailFolders/f1/move"
    assert graph.post.call_args.kwargs["json"] == {"destinationId": "archive"}


# ---- delete_folder ---------------------------------------------------------

def test_delete_folder_calls_delete_and_records_before(tmp_path):
    graph = _graph()
    graph.delete.return_value = None
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-delete",
        action="mail.folder.delete",
        drive_id="me",
        item_id="f1",
        args={},
    )
    result = execute_delete_folder(
        op, graph, logger,
        before={
            "id": "f1", "display_name": "Triage", "path": "/Inbox/Triage",
            "parent_id": "inbox", "total_items": 7, "unread_items": 2,
            "child_folder_count": 0,
        },
    )
    assert result.status == "ok"
    assert result.after is None
    assert graph.delete.call_args.args[0] == "/me/mailFolders/f1"

    entries = list(iter_audit_entries(logger))
    assert entries[0]["before"]["display_name"] == "Triage"


# ---- app-only routing ------------------------------------------------------

def test_create_folder_app_only_routes_via_users_upn(tmp_path):
    graph = _graph()
    graph.post.return_value = {"id": "x", "displayName": "Y"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-ao",
        action="mail.folder.create",
        drive_id="bob@example.com",
        item_id="inbox",
        args={"name": "Y", "parent_path": "/Inbox", "auth_mode": "app-only"},
    )
    execute_create_folder(op, graph, logger, before={})
    assert graph.post.call_args.args[0] == "/users/bob@example.com/mailFolders/inbox/childFolders"


# ---- failure modes ---------------------------------------------------------

def test_rename_folder_missing_new_name_raises(tmp_path):
    graph = _graph()
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-bad",
        action="mail.folder.rename",
        drive_id="me", item_id="f1",
        args={},
    )
    with pytest.raises(KeyError):
        execute_rename_folder(op, graph, logger, before={"display_name": "X"})
