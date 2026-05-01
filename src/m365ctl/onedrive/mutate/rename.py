"""OneDrive RENAME via Graph PATCH .../items/{id} with {'name': ...}."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.batch import EagerSession, GraphCaller
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation


@dataclass(frozen=True)
class RenameResult:
    op_id: str
    status: str
    error: str | None = None
    after: dict[str, Any] | None = None


def start_rename(
    op: Operation,
    client: GraphCaller,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
):
    """Log start, buffer the rename PATCH, return ``(future, after)``."""
    new_name = op.args["new_name"]
    log_mutation_start(
        logger, op_id=op.op_id, cmd="od-rename",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id,
        before=before,
    )
    f = client.patch(
        f"/drives/{op.drive_id}/items/{op.item_id}",
        json_body={"name": new_name},
    )
    after: dict[str, Any] = {
        "parent_path": before.get("parent_path", ""),
        "name": new_name,
    }
    return f, after


def finish_rename(
    op: Operation,
    future,
    after: dict[str, Any],
    logger: AuditLogger,
) -> RenameResult:
    """Resolve future, populate ``after.name`` from response, log end."""
    try:
        body = future.result()
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return RenameResult(op_id=op.op_id, status="error", error=str(e))
    after = {**after, "name": body.get("name", after.get("name", ""))}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return RenameResult(op_id=op.op_id, status="ok", after=after)


def execute_rename(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> RenameResult:
    """Single-op convenience for non-batched callers."""
    eager = EagerSession(graph)
    f, after = start_rename(op, eager, logger, before=before)
    return finish_rename(op, f, after, logger)
