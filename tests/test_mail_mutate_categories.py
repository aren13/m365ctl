"""Tests for m365ctl.mail.mutate.categories."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.models import Category
from m365ctl.mail.mutate.categories import (
    compute_sync_plan,
    execute_add_category,
    execute_remove_category,
    execute_update_category,
)


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def _graph() -> MagicMock:
    return MagicMock()


# ---- add -------------------------------------------------------------------

def test_add_category_posts_and_records_after(tmp_path):
    graph = _graph()
    graph.post.return_value = {"id": "new-cat", "displayName": "Waiting", "color": "preset0"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-add",
        action="mail.categories.add",
        drive_id="me",
        item_id="",
        args={"name": "Waiting", "color": "preset0"},
    )
    result = execute_add_category(op, graph, logger, before={})
    assert result.status == "ok"
    assert result.after == {"id": "new-cat", "display_name": "Waiting", "color": "preset0"}
    assert graph.post.call_args.args[0] == "/me/outlook/masterCategories"
    assert graph.post.call_args.kwargs["json"] == {"displayName": "Waiting", "color": "preset0"}


# ---- update ----------------------------------------------------------------

def test_update_category_patches_and_records_before(tmp_path):
    graph = _graph()
    graph.patch.return_value = {"id": "c1", "displayName": "Waiting-New", "color": "preset2"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-update",
        action="mail.categories.update",
        drive_id="me",
        item_id="c1",
        args={"name": "Waiting-New", "color": "preset2"},
    )
    result = execute_update_category(
        op, graph, logger,
        before={"display_name": "Waiting", "color": "preset0"},
    )
    assert result.status == "ok"
    assert result.after == {"display_name": "Waiting-New", "color": "preset2"}
    assert graph.patch.call_args.args[0] == "/me/outlook/masterCategories/c1"
    assert graph.patch.call_args.kwargs["json_body"] == {
        "displayName": "Waiting-New", "color": "preset2",
    }


def test_update_category_partial_name_only(tmp_path):
    graph = _graph()
    graph.patch.return_value = {"id": "c1", "displayName": "X"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-partial",
        action="mail.categories.update",
        drive_id="me",
        item_id="c1",
        args={"name": "X"},
    )
    execute_update_category(op, graph, logger, before={"display_name": "W", "color": "preset0"})
    assert graph.patch.call_args.kwargs["json_body"] == {"displayName": "X"}


# ---- remove ----------------------------------------------------------------

def test_remove_category_deletes_and_records_before(tmp_path):
    graph = _graph()
    graph.delete.return_value = None
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-remove",
        action="mail.categories.remove",
        drive_id="me",
        item_id="c1",
        args={},
    )
    result = execute_remove_category(
        op, graph, logger,
        before={"display_name": "Waiting", "color": "preset0", "messages_with_category": []},
    )
    assert result.status == "ok"
    assert result.after is None
    assert graph.delete.call_args.args[0] == "/me/outlook/masterCategories/c1"

    entries = list(iter_audit_entries(logger))
    assert entries[0]["before"]["display_name"] == "Waiting"


# ---- sync ------------------------------------------------------------------

def test_compute_sync_plan_add_missing():
    live = [
        Category(id="c1", display_name="Followup", color="preset0"),
    ]
    desired = ["Followup", "Waiting", "Done"]
    plan = compute_sync_plan(live, desired, default_color="preset1")
    assert len(plan) == 2
    actions = [op["action"] for op in plan]
    assert actions == ["mail.categories.add", "mail.categories.add"]
    names = [op["args"]["name"] for op in plan]
    assert names == ["Waiting", "Done"]
    assert all(op["args"]["color"] == "preset1" for op in plan)


def test_compute_sync_plan_no_removal_of_extras():
    live = [
        Category(id="c1", display_name="Followup", color="preset0"),
        Category(id="c2", display_name="LegacyUserCat", color="preset3"),
    ]
    desired = ["Followup"]
    plan = compute_sync_plan(live, desired, default_color="preset0")
    assert plan == []


def test_compute_sync_plan_case_insensitive_match():
    live = [Category(id="c1", display_name="followup", color="preset0")]
    desired = ["Followup"]
    plan = compute_sync_plan(live, desired, default_color="preset0")
    assert plan == []
