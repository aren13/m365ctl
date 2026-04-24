"""Send executors — send_draft (existing) + send_new (inline).

Both invoke Graph endpoints that return 202 Accepted with no response body;
``internet_message_id`` cannot be recovered from that call directly.
``after`` captures ``sent_at`` (local UTC timestamp) and an empty
``internet_message_id`` — Phase 7 catalog crawl backfills.

``mail.send`` is Irreversible (Dispatcher registers in mail/mutate/undo.py).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.compose import BodyFormatError, build_message_payload
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.mutate._common import MailResult


def _user_base(op: Operation) -> str:
    auth_mode = op.args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def execute_send_draft(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /messages/{id}/send (202 Accepted)."""
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-send",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.post_raw(f"{ub}/messages/{op.item_id}/send")
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"sent_at": _now_utc_iso(), "internet_message_id": ""}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_send_new(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /sendMail with {message, saveToSentItems: true}."""
    ub = _user_base(op)
    try:
        message = build_message_payload(
            subject=op.args.get("subject", ""),
            body=op.args.get("body", ""),
            body_type=op.args.get("body_type", "text"),
            to=list(op.args.get("to", [])),
            cc=list(op.args.get("cc", []) or []),
            bcc=list(op.args.get("bcc", []) or []),
            importance=op.args.get("importance"),
            require_subject=True,
        )
    except BodyFormatError as e:
        log_mutation_start(
            logger, op_id=op.op_id, cmd="mail-send",
            args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
        )
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))

    payload = {"message": message, "saveToSentItems": True}
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-send",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.post_raw(f"{ub}/sendMail", json_body=payload)
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"sent_at": _now_utc_iso(), "internet_message_id": ""}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
