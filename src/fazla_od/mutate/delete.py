"""OneDrive DELETE (recycle) + RESTORE (from recycle).

Spec §7 rule 6: no hard deletes here. The Graph ``DELETE
/drives/{d}/items/{i}`` endpoint on OneDrive is a SOFT delete — the item
goes to the recycle bin. Hard-delete lives in ``mutate/clean.py``
(``od-clean recycle-bin``).

**Restore caveat (discovered during Plan 4 live smoke test):** Microsoft
Graph v1.0's ``POST /drives/{d}/items/{i}/restore`` is documented as
**OneDrive Personal only**. OneDrive-for-Business recycle-bin items have
no public Graph v1.0 restore endpoint — the supported paths are the
SharePoint REST API (``/Web/RecycleBin('<id>')/Restore()``) or
PnP.PowerShell (``Restore-PnPRecycleBinItem``). ``execute_restore``
still issues the Graph call since it does work for the Personal case
and for some tenant configurations, but translates Graph's
``notSupported`` / ``BadRequest`` response into an actionable error
message that points the operator at the documented workaround.
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


_ODFB_RESTORE_MANUAL = (
    "Graph v1.0 /restore is OneDrive-Personal-only; "
    "OneDrive-for-Business items must be restored via SharePoint web UI "
    "(Recycle bin → Restore) or PnP.PowerShell "
    "(Restore-PnPRecycleBinItem). The original parent path is recorded in "
    "the audit log's 'before.parent_path' field for this op_id."
)

# Graph error codes that signal "this is the OneDrive-for-Business no-public-
# restore-endpoint case"; rewrite them into an actionable operator message.
_ODFB_RESTORE_TOKENS = ("notSupported", "BadRequest", "invalidRequest")


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
        err = str(e)
        if any(t in err for t in _ODFB_RESTORE_TOKENS):
            err = f"{err} | {_ODFB_RESTORE_MANUAL}"
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=err)
        return DeleteResult(op_id=op.op_id, status="error", error=err)
    after = {
        "parent_path": (data.get("parentReference") or {}).get("path", ""),
        "name": data.get("name", before.get("name", "")),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return DeleteResult(op_id=op.op_id, status="ok", after=after)
