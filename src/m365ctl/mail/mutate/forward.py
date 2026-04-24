"""Forward executors — create_forward + send_forward_inline."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.compose import parse_recipients
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.mutate._common import MailResult


def _user_base(op: Operation) -> str:
    auth_mode = op.args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def execute_create_forward(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-forward(create)",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        draft = graph.post(f"{ub}/messages/{op.item_id}/createForward", json={})
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"draft_id": draft.get("id", "")}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_send_forward_inline(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    ub = _user_base(op)
    payload: dict[str, Any] = {"comment": op.args.get("body", "")}
    recipients = op.args.get("to") or []
    if recipients:
        payload["toRecipients"] = parse_recipients(list(recipients))
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-forward(inline)",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.post_raw(f"{ub}/messages/{op.item_id}/forward", json_body=payload)
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"sent_at": _now_utc_iso(), "internet_message_id": ""}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
