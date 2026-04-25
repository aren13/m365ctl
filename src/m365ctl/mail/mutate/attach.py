"""Attachment write executors — add (small) + remove + pick_upload_strategy.

Large-attachment upload session (>=3 MB) deferred to Phase 5a-2.
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any, Literal

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.mutate._common import MailResult

# Phase 5a-2: re-exported alias so external callers can refer to the
# attachment-flavoured result type without coupling to the shared MailResult.
AttachResult = MailResult


_SMALL_THRESHOLD_BYTES = 3 * 1024 * 1024
CHUNK_SIZE_BYTES = 4 * 1024 * 1024  # 4 MB; multiple of 320 KB.


def _user_base(op: Operation) -> str:
    auth_mode = op.args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)


def pick_upload_strategy(*, size: int) -> Literal["small", "large"]:
    """Choose upload strategy. < 3 MB -> small; >= 3 MB -> large."""
    return "small" if size < _SMALL_THRESHOLD_BYTES else "large"


def execute_add_attachment_small(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /messages/{id}/attachments with fileAttachment + base64 content."""
    ub = _user_base(op)
    name = op.args["name"]
    content_type = op.args.get("content_type", "application/octet-stream")
    content_b64 = op.args["content_bytes_b64"]

    try:
        raw = base64.b64decode(content_b64)
    except Exception as e:
        log_mutation_start(
            logger, op_id=op.op_id, cmd="mail-attach-add",
            args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
        )
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error",
                         error=f"invalid base64 content: {e}")
        return MailResult(op_id=op.op_id, status="error",
                          error=f"invalid base64 content: {e}")

    content_hash = hashlib.sha256(raw).hexdigest()
    payload: dict[str, Any] = {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": name,
        "contentType": content_type,
        "contentBytes": content_b64,
    }

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-attach-add",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        created = graph.post(f"{ub}/messages/{op.item_id}/attachments", json=payload)
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {
        "id": created.get("id", ""),
        "name": created.get("name", name),
        "size": created.get("size", len(raw)),
        "content_hash": content_hash,
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_remove_attachment(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """DELETE /messages/{id}/attachments/{aid}. ``before`` captures full bytes for undo."""
    ub = _user_base(op)
    attachment_id = op.args["attachment_id"]
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-attach-remove",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.delete(f"{ub}/messages/{op.item_id}/attachments/{attachment_id}")
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    log_mutation_end(logger, op_id=op.op_id, after=None, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=None)


def execute_add_attachment_large(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> AttachResult:
    """Upload a large attachment via Graph's upload-session protocol.

    Three Graph calls:
      1. POST createUploadSession -> {uploadUrl}.
      2. PUT each chunk to uploadUrl with Content-Range header.
      3. Final PUT returns the attachment metadata.

    The file is streamed chunk-by-chunk from ``args["file_path"]``; we
    never materialise it (or its base64 form) in memory or in the audit
    record.
    """
    args = op.args
    auth_mode = args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    ub = user_base(spec, auth_mode=auth_mode)
    file_path = Path(args["file_path"])
    name = args["name"]
    content_type = args.get("content_type", "application/octet-stream")
    size = int(args.get("size") or file_path.stat().st_size)

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-attach-add-large",
        args={k: v for k, v in args.items() if k != "content_bytes_b64"},
        drive_id=op.drive_id, item_id=op.item_id, before=before,
    )

    # 1. Create the upload session.
    create_path = f"{ub}/messages/{op.item_id}/attachments/createUploadSession"
    try:
        sess = graph.post(create_path, json={
            "AttachmentItem": {
                "attachmentType": "file",
                "name": name,
                "size": size,
                "contentType": content_type,
            },
        })
    except GraphError as e:
        log_mutation_end(
            logger, op_id=op.op_id, after=None, result="error", error=str(e),
        )
        return AttachResult(op_id=op.op_id, status="error", error=str(e))

    upload_url = sess["uploadUrl"]

    # 2. Stream chunks.
    final_body: dict = {}
    offset = 0
    try:
        with file_path.open("rb") as fh:
            while offset < size:
                chunk = fh.read(CHUNK_SIZE_BYTES)
                if not chunk:
                    break
                end = offset + len(chunk) - 1
                body, status = graph.put_chunk(
                    upload_url, chunk,
                    content_range=f"bytes {offset}-{end}/{size}",
                    content_length=len(chunk),
                )
                offset = end + 1
                if status in (200, 201):
                    final_body = body
                    break
    except GraphError as e:
        log_mutation_end(
            logger, op_id=op.op_id,
            after={"upload_url": upload_url, "uploaded_bytes": offset},
            result="error", error=str(e),
        )
        return AttachResult(op_id=op.op_id, status="error", error=str(e))

    after: dict[str, Any] = {
        "id": final_body.get("id", ""),
        "name": final_body.get("name", name),
        "size": final_body.get("size", size),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return AttachResult(op_id=op.op_id, status="ok", after=after)
