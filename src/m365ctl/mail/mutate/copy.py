"""Message copy — POST /messages/{id}/copy with {destinationId}.

Inverse: `mail.delete.soft` on ``after.new_message_id``. The inverse executor
arrives in Phase 4; Phase 3 registers only the Dispatcher entry.
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


def execute_copy(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    dest_id = op.args["destination_id"]
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-copy",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        created = graph.post(
            f"{ub}/messages/{op.item_id}/copy",
            json={"destinationId": dest_id},
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {
        "new_message_id": created.get("id", ""),
        "destination_folder_id": dest_id,
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
