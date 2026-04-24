"""OneDrive RENAME via Graph PATCH .../items/{id} with {'name': ...}."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fazla_od.audit import AuditLogger, log_mutation_end, log_mutation_start
from fazla_od.graph import GraphClient, GraphError
from fazla_od.planfile import Operation


@dataclass(frozen=True)
class RenameResult:
    op_id: str
    status: str
    error: str | None = None
    after: dict[str, Any] | None = None


def execute_rename(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> RenameResult:
    new_name = op.args["new_name"]
    log_mutation_start(
        logger, op_id=op.op_id, cmd="od-rename",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id,
        before=before,
    )
    try:
        result = graph.patch(
            f"/drives/{op.drive_id}/items/{op.item_id}",
            json_body={"name": new_name},
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return RenameResult(op_id=op.op_id, status="error", error=str(e))
    after = {
        "parent_path": before.get("parent_path", ""),
        "name": result.get("name", new_name),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return RenameResult(op_id=op.op_id, status="ok", after=after)
