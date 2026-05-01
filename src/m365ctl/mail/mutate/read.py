"""Message read/unread — PATCH /messages/{id} with {isRead: bool}."""
from __future__ import annotations

from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.batch import EagerSession, GraphCaller
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.endpoints import user_base_for_op
from m365ctl.mail.mutate._common import MailResult


def start_read(
    op: Operation,
    client: GraphCaller,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
):
    """Log start, buffer the isRead PATCH, return ``(future, after)``."""
    is_read = bool(op.args["is_read"])
    ub = user_base_for_op(op)
    headers = {}
    change_key = op.args.get("change_key")
    if change_key:
        headers["If-Match"] = change_key

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-read",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    f = client.patch(
        f"{ub}/messages/{op.item_id}",
        json_body={"isRead": is_read},
        headers=headers or None,
    )
    after: dict[str, Any] = {"is_read": is_read}
    return f, after


def finish_read(
    op: Operation,
    future,
    after: dict[str, Any],
    logger: AuditLogger,
) -> MailResult:
    """Resolve future, log end, return ``MailResult``."""
    try:
        future.result()
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_read(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """Single-op convenience for non-batched callers (e.g., --message-id mode)."""
    eager = EagerSession(graph)
    f, after = start_read(op, eager, logger, before=before)
    return finish_read(op, f, after, logger)
