"""OneDrive MOVE via Graph PATCH .../items/{id}.

A MOVE is a PATCH with a sparse ``parentReference.id`` body. The item_id
does not change across a move — handy for audit log idempotency and
undo.

Verb shape (start/finish/execute trio — see
``docs/superpowers/specs/2026-05-01-graph-batch-support-design.md``):

- ``start_move(op, client, logger, *, before)`` — log start, buffer the
  HTTP call, return ``(future, after)``. ``client`` is a ``GraphCaller``
  (``BatchSession`` for bulk, ``EagerSession`` for single-op).
- ``finish_move(op, future, after, logger)`` — resolve the future, log
  end, return ``MoveResult``.
- ``execute_move(op, graph, logger, *, before)`` — single-op convenience
  that wraps a ``GraphClient`` in ``EagerSession`` and chains
  ``start_move`` + ``finish_move``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.batch import EagerSession, GraphCaller
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation


@dataclass(frozen=True)
class MoveResult:
    op_id: str
    status: str  # "ok" | "error"
    error: str | None = None
    after: dict[str, Any] | None = None


def start_move(
    op: Operation,
    client: GraphCaller,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
):
    """Log start, buffer the move PATCH, return ``(future, after)``.

    ``after.parent_path`` / ``after.name`` are filled in by ``finish_move``
    from the Graph response body.
    """
    new_parent = op.args["new_parent_item_id"]
    log_mutation_start(
        logger, op_id=op.op_id, cmd="od-move",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id,
        before=before,
    )
    f = client.patch(
        f"/drives/{op.drive_id}/items/{op.item_id}",
        json_body={"parentReference": {"id": new_parent}},
    )
    after: dict[str, Any] = {
        "parent_path": "",
        "name": before.get("name", ""),
        "parent_id": new_parent,
    }
    return f, after


def finish_move(
    op: Operation,
    future,
    after: dict[str, Any],
    logger: AuditLogger,
) -> MoveResult:
    """Resolve future, populate ``after`` from response, log end."""
    try:
        body = future.result()
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return MoveResult(op_id=op.op_id, status="error", error=str(e))
    after = {
        **after,
        "parent_path": (body.get("parentReference") or {}).get("path", ""),
        "name": body.get("name", after.get("name", "")),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MoveResult(op_id=op.op_id, status="ok", after=after)


def execute_move(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MoveResult:
    """Execute a single move op, logging start BEFORE, end AFTER.

    Spec §7 rule 5 invariant: the 'start' record is persisted before the
    Graph call, so a crash mid-call still leaves a trail. The start/finish
    split makes this guarantee explicit.
    """
    eager = EagerSession(graph)
    f, after = start_move(op, eager, logger, before=before)
    return finish_move(op, f, after, logger)
