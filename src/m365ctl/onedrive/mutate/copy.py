"""OneDrive COPY via Graph POST .../items/{id}/copy (async).

Graph responds 202 with a ``Location`` header pointing at a monitor URL.
On the single-item path (``execute_copy``) we poll the monitor until
status == 'completed' (or 'failed'). On the bulk path
(``start_copy``/``finish_copy``) we only capture the monitor URL into
``after["monitor_url"]`` — polling stays out-of-band so a 200-op plan
doesn't have to serialize on N async jobs.

In both flows the audit ``after`` block records ``new_item_id`` (resolved
when polling completes) or ``monitor_url`` (bulk path) so undo can find
the copy to delete it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.batch import GraphCaller
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation


@dataclass(frozen=True)
class CopyResult:
    op_id: str
    status: str
    error: str | None = None
    after: dict[str, Any] | None = None


def start_copy(
    op: Operation,
    client: GraphCaller,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
):
    """Log start, buffer the copy POST, return ``(future, after)``.

    ``after.monitor_url`` is populated by ``finish_copy`` from the 202
    response's ``Location`` header. Polling is the caller's responsibility
    in the bulk path — the audit log records the monitor URL so an
    operator (or a follow-up command) can resolve outcomes async.
    """
    target_drive = op.args["target_drive_id"]
    target_parent = op.args["target_parent_item_id"]
    new_name = op.args.get("new_name", before.get("name", ""))
    log_mutation_start(
        logger, op_id=op.op_id, cmd="od-copy",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id,
        before=before,
    )
    f = client.post(
        f"/drives/{op.drive_id}/items/{op.item_id}/copy",
        json={
            "parentReference": {"driveId": target_drive, "id": target_parent},
            "name": new_name,
        },
    )
    after: dict[str, Any] = {
        "new_item_id": "",
        "new_name": new_name,
        "target_drive_id": target_drive,
        "target_parent_item_id": target_parent,
        "monitor_url": "",
    }
    return f, after


def finish_copy(
    op: Operation,
    future,
    after: dict[str, Any],
    logger: AuditLogger,
) -> CopyResult:
    """Resolve future, capture ``Location`` header, log end.

    Graph copy is async (202). ``finish_copy`` does NOT poll the monitor
    URL — it just records it in ``after`` so undo / audit consumers can
    resolve the outcome later. Synchronous polling lives in
    ``execute_copy`` (single-item path).
    """
    try:
        future.result()
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return CopyResult(op_id=op.op_id, status="error", error=str(e))
    monitor_url = (future.headers() or {}).get("Location", "")
    after = {**after, "monitor_url": monitor_url}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return CopyResult(op_id=op.op_id, status="ok", after=after)


def execute_copy(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
    poll_interval: float = 2.0,
    max_wait_seconds: float = 300.0,
) -> CopyResult:
    target_drive = op.args["target_drive_id"]
    target_parent = op.args["target_parent_item_id"]
    new_name = op.args.get("new_name", before.get("name", ""))

    log_mutation_start(
        logger, op_id=op.op_id, cmd="od-copy",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id,
        before=before,
    )

    try:
        resp = graph.post_raw(
            f"/drives/{op.drive_id}/items/{op.item_id}/copy",
            json_body={
                "parentReference": {"driveId": target_drive, "id": target_parent},
                "name": new_name,
            },
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return CopyResult(op_id=op.op_id, status="error", error=str(e))

    monitor_url = resp.headers.get("Location")
    if resp.status_code == 200 and not monitor_url:
        body = resp.json() if resp.content else {}
        after = {"new_item_id": body.get("id", ""), "new_name": new_name,
                 "target_drive_id": target_drive,
                 "target_parent_item_id": target_parent}
        log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
        return CopyResult(op_id=op.op_id, status="ok", after=after)

    if not monitor_url:
        err = f"copy POST returned {resp.status_code} with no Location header"
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=err)
        return CopyResult(op_id=op.op_id, status="error", error=err)

    waited = 0.0
    while True:
        try:
            status_body = graph.get_absolute(monitor_url)
        except GraphError as e:
            log_mutation_end(logger, op_id=op.op_id, after=None,
                             result="error", error=str(e))
            return CopyResult(op_id=op.op_id, status="error", error=str(e))
        status = status_body.get("status")
        if status == "completed":
            after = {
                "new_item_id": status_body.get("resourceId", ""),
                "new_name": new_name,
                "target_drive_id": target_drive,
                "target_parent_item_id": target_parent,
            }
            log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
            return CopyResult(op_id=op.op_id, status="ok", after=after)
        if status == "failed":
            err = f"copy job failed: {status_body!r}"
            log_mutation_end(logger, op_id=op.op_id, after=None,
                             result="error", error=err)
            return CopyResult(op_id=op.op_id, status="error", error=err)

        if waited >= max_wait_seconds:
            err = f"copy timeout after {waited}s (last status {status!r})"
            log_mutation_end(logger, op_id=op.op_id, after=None,
                             result="error", error=err)
            return CopyResult(op_id=op.op_id, status="error", error=err)
        graph._sleep(poll_interval)
        waited += poll_interval
