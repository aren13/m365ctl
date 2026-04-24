"""Soft delete — move message to the Deleted Items folder.

Graph's convention: the literal string ``"deleteditems"`` is the well-known
folder alias. POSTing to ``/messages/{id}/move`` with ``destinationId="deleteditems"``
moves the message into the mailbox's Deleted Items folder. Nothing is
permanently removed — a later ``m365ctl undo`` can move it back.

Hard delete (``mail-clean``) — a separate verb arriving Phase 6 — uses
``DELETE /messages/{id}`` which bypasses Deleted Items. Do not confuse the two.
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


def execute_soft_delete(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /messages/{id}/move {destinationId: "deleteditems"}.

    ``before`` must contain ``parent_folder_id`` + ``parent_folder_path`` for
    undo to place the message back. The CLI layer fetches these via
    ``get_message`` before calling. If the pre-fetch fails, empty ``before``
    is acceptable — the delete still succeeds; undo degrades to best-effort.
    """
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-delete-soft",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        response = graph.post(
            f"{ub}/messages/{op.item_id}/move",
            json={"destinationId": "deleteditems"},
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {
        "parent_folder_id": response.get("parentFolderId", ""),
        "deleted_from": before.get("parent_folder_id", ""),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
