"""Message move — POST /messages/{id}/move with {destinationId}.

Verb shape (canonical for the rest of mail.mutate):
- ``start_move(op, client, logger, *, before)`` — log start, buffer the
  POST, return ``(future, after)``. ``client`` is a ``GraphCaller`` (a
  ``BatchSession`` for bulk/from-plan execution, or an ``EagerSession``
  wrapping a ``GraphClient`` for single-op execution).
- ``finish_move(op, future, after, logger)`` — resolve the future, log
  end, return ``MailResult``.
- ``execute_move(op, graph, logger, *, before)`` — single-op convenience
  that wraps a ``GraphClient`` in ``EagerSession`` and chains
  start_move + finish_move.
"""
from __future__ import annotations

from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.batch import EagerSession, GraphCaller
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.mutate._common import MailResult


def _user_base(op: Operation) -> str:
    auth_mode = op.args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)


def start_move(
    op: Operation,
    client: GraphCaller,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
):
    """Log start, buffer the move POST, return ``(future, after)``."""
    dest_id = op.args["destination_id"]
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-move",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    f = client.post(
        f"{ub}/messages/{op.item_id}/move",
        json={"destinationId": dest_id},
    )
    after: dict[str, Any] = {"parent_folder_id": dest_id}
    return f, after


def finish_move(
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


def execute_move(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """Single-op convenience for non-batched callers (e.g., --message-id mode)."""
    eager = EagerSession(graph)
    f, after = start_move(op, eager, logger, before=before)
    return finish_move(op, f, after, logger)
