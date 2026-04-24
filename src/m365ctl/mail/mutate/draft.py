"""Draft CRUD — create/update/delete, all undoable."""
from __future__ import annotations

from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.compose import build_message_payload, parse_recipients
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.mutate._common import MailResult


def _user_base(op: Operation) -> str:
    auth_mode = op.args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)


def execute_create_draft(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /messages with the composed payload; Graph creates it in Drafts."""
    ub = _user_base(op)
    payload = build_message_payload(
        subject=op.args.get("subject", ""),
        body=op.args.get("body", ""),
        body_type=op.args.get("body_type", "text"),
        to=list(op.args.get("to", [])),
        cc=list(op.args.get("cc", []) or []),
        bcc=list(op.args.get("bcc", []) or []),
        importance=op.args.get("importance"),
    )
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-draft-create",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        created = graph.post(f"{ub}/messages", json=payload)
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {
        "id": created.get("id", ""),
        "web_link": created.get("webLink", ""),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_update_draft(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """PATCH /messages/{id} with the subset of fields specified in op.args."""
    ub = _user_base(op)
    payload: dict[str, Any] = {}
    if "subject" in op.args:
        payload["subject"] = op.args["subject"]
    if "body" in op.args:
        payload["body"] = {
            "contentType": op.args.get("body_type", "text"),
            "content": op.args["body"],
        }
    if "to" in op.args:
        payload["toRecipients"] = parse_recipients(list(op.args["to"]))
    if "cc" in op.args:
        payload["ccRecipients"] = parse_recipients(list(op.args["cc"]))
    if "bcc" in op.args:
        payload["bccRecipients"] = parse_recipients(list(op.args["bcc"]))
    if "importance" in op.args:
        payload["importance"] = op.args["importance"]

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-draft-update",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.patch(f"{ub}/messages/{op.item_id}", json_body=payload)
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    log_mutation_end(logger, op_id=op.op_id, after={"updated": True}, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after={"updated": True})


def execute_delete_draft(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """DELETE /messages/{id}. ``before`` MUST contain full draft for undo."""
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-draft-delete",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.delete(f"{ub}/messages/{op.item_id}")
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    log_mutation_end(logger, op_id=op.op_id, after=None, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=None)
