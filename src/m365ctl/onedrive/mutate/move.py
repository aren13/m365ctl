"""OneDrive MOVE via Graph PATCH .../items/{id}.

A MOVE is a PATCH with a sparse ``parentReference.id`` body. The item_id
does not change across a move — handy for audit log idempotency and
undo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation


@dataclass(frozen=True)
class MoveResult:
    op_id: str
    status: str  # "ok" | "error"
    error: str | None = None
    after: dict[str, Any] | None = None


def execute_move(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MoveResult:
    """Execute a single move op, logging start BEFORE, end AFTER.

    Spec §7 rule 5 invariant: the 'start' record is persisted before the
    Graph call, so a crash mid-call still leaves a trail. We wrap the
    Graph call in try/except to guarantee a matching 'end' record.
    """
    new_parent = op.args["new_parent_item_id"]
    log_mutation_start(
        logger, op_id=op.op_id, cmd="od-move",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id,
        before=before,
    )
    try:
        result = graph.patch(
            f"/drives/{op.drive_id}/items/{op.item_id}",
            json_body={"parentReference": {"id": new_parent}},
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return MoveResult(op_id=op.op_id, status="error", error=str(e))
    after = {
        "parent_path": (result.get("parentReference") or {}).get("path", ""),
        "name": result.get("name", before.get("name", "")),
        "parent_id": new_parent,
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MoveResult(op_id=op.op_id, status="ok", after=after)
