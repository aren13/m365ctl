"""OneDrive DELETE (recycle) + RESTORE (from recycle).

Spec §7 rule 6: no hard deletes here. The Graph ``DELETE
/drives/{d}/items/{i}`` endpoint on OneDrive is a SOFT delete — the item
goes to the recycle bin. Hard-delete lives in ``mutate/clean.py``
(``od-clean recycle-bin``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fazla_od.audit import AuditLogger, log_mutation_end, log_mutation_start
from fazla_od.graph import GraphClient, GraphError
from fazla_od.planfile import Operation


@dataclass(frozen=True)
class DeleteResult:
    op_id: str
    status: str
    error: str | None = None
    after: dict[str, Any] | None = None


def execute_recycle_delete(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> DeleteResult:
    log_mutation_start(
        logger, op_id=op.op_id, cmd="od-delete",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id,
        before=before,
    )
    try:
        graph.delete(f"/drives/{op.drive_id}/items/{op.item_id}")
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return DeleteResult(op_id=op.op_id, status="error", error=str(e))
    after = {"parent_path": "(recycle bin)", "name": before.get("name", ""),
             "recycled_from": before.get("parent_path", "")}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return DeleteResult(op_id=op.op_id, status="ok", after=after)


def execute_restore(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> DeleteResult:
    log_mutation_start(
        logger, op_id=op.op_id, cmd="od-undo(restore)",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id,
        before=before,
    )
    try:
        body = {"parentReference": {"id": op.args["parent_item_id"]}} \
            if "parent_item_id" in op.args else None
        resp = graph.post_raw(
            f"/drives/{op.drive_id}/items/{op.item_id}/restore",
            json_body=body,
        )
        data = resp.json() if resp.content else {}
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return DeleteResult(op_id=op.op_id, status="error", error=str(e))
    after = {
        "parent_path": (data.get("parentReference") or {}).get("path", ""),
        "name": data.get("name", before.get("name", "")),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return DeleteResult(op_id=op.op_id, status="ok", after=after)
