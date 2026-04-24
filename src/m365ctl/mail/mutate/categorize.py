"""Message categorize — PATCH /messages/{id} with {categories: [...]}.

The CLI layer resolves ``--add``/``--remove``/``--set`` into a concrete final
list before calling ``execute_categorize``. The executor itself is a pure
set-categories operation.
"""
from __future__ import annotations

from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.mutate._common import MailResult


def _user_base(op: Operation) -> str:
    auth_mode = op.args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)


def execute_categorize(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    new_categories = list(op.args["categories"])
    ub = _user_base(op)
    headers = {}
    change_key = op.args.get("change_key")
    if change_key:
        headers["If-Match"] = change_key

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-categorize",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.patch(
            f"{ub}/messages/{op.item_id}",
            json_body={"categories": new_categories},
            headers=headers or None,
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"categories": new_categories}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
