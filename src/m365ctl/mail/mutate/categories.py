"""Master-category mutations — add, update, remove, sync.

``sync`` is a pure function: given a live category list and a desired list,
it returns a list of ``mail.categories.add`` op specs to bring the live set
up to the desired one. It NEVER emits ``remove`` ops — the spec says sync
reconciles toward the config set, but removing user-created categories not
in config would surprise users.
"""
from __future__ import annotations

from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.models import Category
from m365ctl.mail.mutate._common import MailResult


def _user_base(op: Operation) -> str:
    auth_mode = op.args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)


def execute_add_category(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /outlook/masterCategories with {displayName, color}."""
    name = op.args["name"]
    color = op.args.get("color", "preset0")
    ub = _user_base(op)

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-categories-add",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        created = graph.post(
            f"{ub}/outlook/masterCategories",
            json={"displayName": name, "color": color},
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {
        "id": created.get("id", ""),
        "display_name": created.get("displayName", name),
        "color": created.get("color", color),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_update_category(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """PATCH /outlook/masterCategories/{id} with {displayName?, color?}."""
    ub = _user_base(op)
    payload: dict[str, Any] = {}
    if "name" in op.args:
        payload["displayName"] = op.args["name"]
    if "color" in op.args:
        payload["color"] = op.args["color"]

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-categories-update",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        result = graph.patch(
            f"{ub}/outlook/masterCategories/{op.item_id}",
            json_body=payload,
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {}
    if "displayName" in result:
        after["display_name"] = result["displayName"]
    if "color" in result:
        after["color"] = result["color"]
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_remove_category(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """DELETE /outlook/masterCategories/{id}."""
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-categories-remove",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.delete(f"{ub}/outlook/masterCategories/{op.item_id}")
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    log_mutation_end(logger, op_id=op.op_id, after=None, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=None)


def compute_sync_plan(
    live: list[Category],
    desired: list[str],
    *,
    default_color: str = "preset0",
) -> list[dict[str, Any]]:
    """Return a list of ``mail.categories.add`` op-spec dicts for names
    in ``desired`` but not in ``live``. Never emits removals.

    Matching is case-insensitive on display name.
    """
    have = {c.display_name.casefold() for c in live}
    plan: list[dict[str, Any]] = []
    for name in desired:
        if name.casefold() in have:
            continue
        plan.append({
            "op_id": new_op_id(),
            "action": "mail.categories.add",
            "drive_id": "me",
            "item_id": "",
            "args": {"name": name, "color": default_color},
        })
    return plan
