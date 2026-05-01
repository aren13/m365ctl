"""Message copy — POST /messages/{id}/copy with {destinationId}.

Inverse: `mail.delete.soft` on ``after.new_message_id``. The inverse executor
arrives in Phase 4; Phase 3 registers only the Dispatcher entry.
"""
from __future__ import annotations

from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.batch import EagerSession, GraphCaller
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.endpoints import user_base_for_op
from m365ctl.mail.mutate._common import MailResult


def start_copy(
    op: Operation,
    client: GraphCaller,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
):
    """Log start, buffer the copy POST, return ``(future, after)``.

    ``after.new_message_id`` is filled in by ``finish_copy`` from the Graph
    response body (the new message's id).
    """
    dest_id = op.args["destination_id"]
    ub = user_base_for_op(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-copy",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    f = client.post(
        f"{ub}/messages/{op.item_id}/copy",
        json={"destinationId": dest_id},
    )
    after: dict[str, Any] = {
        "new_message_id": "",
        "destination_folder_id": dest_id,
    }
    return f, after


def finish_copy(
    op: Operation,
    future,
    after: dict[str, Any],
    logger: AuditLogger,
) -> MailResult:
    """Resolve future, populate ``after.new_message_id`` from response, log end."""
    try:
        body = future.result()
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after = {**after, "new_message_id": body.get("id", "")}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_copy(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """Single-op convenience for non-batched callers (e.g., --message-id mode)."""
    eager = EagerSession(graph)
    f, after = start_copy(op, eager, logger, before=before)
    return finish_copy(op, f, after, logger)
