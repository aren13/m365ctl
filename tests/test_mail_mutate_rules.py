"""Tests for m365ctl.mail.mutate.rules — server-side inbox rule executors."""
from __future__ import annotations

from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.mutate.rules import (
    execute_create,
    execute_delete,
    execute_reorder,
    execute_set_enabled,
    execute_update,
)


def _op(action: str, args: dict, item_id: str = "") -> Operation:
    return Operation(
        op_id=new_op_id(),
        action=action,
        drive_id="me",
        item_id=item_id,
        args=args,
        dry_run_result="",
    )


def test_create_posts_body_and_records_id(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {
        "id": "new-rule-1",
        "displayName": "r",
        "sequence": 10,
        "isEnabled": True,
    }
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.rule.create", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "body": {"displayName": "r", "sequence": 10, "isEnabled": True},
    })
    r = execute_create(op, graph, logger, before={})
    assert r.status == "ok"
    assert r.after["id"] == "new-rule-1"
    graph.post.assert_called_once()
    assert "messageRules" in graph.post.call_args.args[0]


def test_update_patches_with_etag(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "rule-1", "displayName": "renamed"}
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.rule.update", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "rule_id": "rule-1",
        "body": {"displayName": "renamed"},
    }, item_id="rule-1")
    r = execute_update(op, graph, logger, before={"displayName": "before"})
    assert r.status == "ok"
    graph.patch.assert_called_once()


def test_delete_calls_graph_delete(tmp_path):
    graph = MagicMock()
    graph.delete.return_value = None
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.rule.delete", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "rule_id": "rule-1",
    }, item_id="rule-1")
    r = execute_delete(op, graph, logger, before={
        "displayName": "r", "sequence": 10, "isEnabled": True,
        "conditions": {}, "actions": {"delete": True}, "exceptions": {},
    })
    assert r.status == "ok"
    graph.delete.assert_called_once()


def test_set_enabled_patches_only_isenabled(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "rule-1", "isEnabled": False}
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.rule.set-enabled", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "rule_id": "rule-1",
        "is_enabled": False,
    }, item_id="rule-1")
    r = execute_set_enabled(op, graph, logger, before={"isEnabled": True})
    assert r.status == "ok"
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"isEnabled": False}


def test_reorder_patches_sequence_per_rule(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "rule-1"}
    # When `before` is empty, executor fetches current ordering for undo;
    # mock that GET returns two rules with current sequence values.
    graph.get.return_value = {
        "value": [
            {"id": "rule-A", "sequence": 1},
            {"id": "rule-B", "sequence": 2},
        ],
    }
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.rule.reorder", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "ordering": [
            {"rule_id": "rule-A", "sequence": 10},
            {"rule_id": "rule-B", "sequence": 20},
        ],
    })
    r = execute_reorder(op, graph, logger, before={})
    assert r.status == "ok"
    assert graph.patch.call_count == 2


def test_executor_propagates_graph_error_as_status(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.post.side_effect = GraphError("InvalidRequest: bad rule body")
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.rule.create", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "body": {"displayName": "x"},
    })
    r = execute_create(op, graph, logger, before={})
    assert r.status == "error"
    assert "InvalidRequest" in (r.error or "")
