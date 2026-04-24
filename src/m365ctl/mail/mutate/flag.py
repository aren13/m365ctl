"""Message flag — PATCH /messages/{id} with {flag: {...}}."""
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


def _build_flag_payload(args: dict[str, Any]) -> dict[str, Any]:
    status = args["status"]
    payload: dict[str, Any] = {"flagStatus": status}
    if args.get("start_at"):
        payload["startDateTime"] = {"dateTime": args["start_at"], "timeZone": "UTC"}
    if args.get("due_at"):
        payload["dueDateTime"] = {"dateTime": args["due_at"], "timeZone": "UTC"}
    return {"flag": payload}


def execute_flag(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    ub = _user_base(op)
    payload = _build_flag_payload(op.args)
    headers = {}
    change_key = op.args.get("change_key")
    if change_key:
        headers["If-Match"] = change_key

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-flag",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.patch(
            f"{ub}/messages/{op.item_id}",
            json_body=payload,
            headers=headers or None,
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {
        "status": op.args["status"],
        "start_at": op.args.get("start_at"),
        "due_at": op.args.get("due_at"),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
