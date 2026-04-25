"""Hard-delete + folder/recycle-bin empty executors.

Each operation captures the full EML(s) to ``[logging].purged_dir`` BEFORE
issuing the Graph DELETE. The captures live at:

    <purged_dir>/<YYYY-MM-DD>/<op_id>.eml          # single hard-delete
    <purged_dir>/<YYYY-MM-DD>/<op_id>/<msg>.eml    # bulk empty

These ops are NOT undoable — ``register_irreversible`` blocks any
``m365ctl undo`` attempt with an error message pointing at the capture.
The capture is the only recovery path outside Graph.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.export.eml import fetch_eml_bytes


@dataclass
class HardDeleteResult:
    op_id: str
    status: str          # "ok" | "error"
    error: str | None = None
    after: dict[str, Any] = field(default_factory=dict)


def _today_dir(purged_dir: Path) -> Path:
    return purged_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _capture_eml(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    message_id: str,
    out_path: Path,
) -> bytes:
    """Fetch + persist EML; return the bytes for audit-log fields."""
    raw = fetch_eml_bytes(
        graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode, message_id=message_id,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw)
    return raw


def _peek_eml_summary(eml: bytes) -> dict[str, str]:
    """Cheap header scan for audit fields. Doesn't fully parse MIME."""
    headers = {"internet_message_id": "", "subject": "", "sender_address": ""}
    for line in eml.splitlines()[:200]:
        try:
            text = line.decode("utf-8", errors="replace")
        except Exception:
            continue
        if not text or text[0] in (" ", "\t"):
            continue  # continuation
        if ":" not in text:
            if not text.strip():
                break  # end of header block
            continue
        key, _, value = text.partition(":")
        k = key.strip().lower()
        v = value.strip()
        if k == "message-id":
            headers["internet_message_id"] = v
        elif k == "subject":
            headers["subject"] = v
        elif k == "from":
            headers["sender_address"] = v
    return headers


def execute_hard_delete(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    purged_dir: Path,
    before: dict | None = None,
) -> HardDeleteResult:
    args = op.args
    mailbox_spec = args["mailbox_spec"]
    auth_mode = args["auth_mode"]
    message_id = args["message_id"]
    capture_path = _today_dir(purged_dir) / f"{op.op_id}.eml"

    # Capture EML first. If we can't (404, auth, etc.), bail BEFORE deleting.
    try:
        eml = _capture_eml(
            graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
            message_id=message_id, out_path=capture_path,
        )
    except GraphError as e:
        log_mutation_start(
            logger, op_id=op.op_id, cmd="mail-delete-hard",
            args=args, drive_id=op.drive_id, item_id=op.item_id,
            before=before or {},
        )
        log_mutation_end(
            logger, op_id=op.op_id, after={}, result="error",
            error=f"EML capture failed before delete: {e}",
        )
        return HardDeleteResult(
            op_id=op.op_id, status="error",
            error=f"EML capture failed; refusing to delete: {e}",
        )

    summary = _peek_eml_summary(eml)
    audit_before = {**(before or {}), **summary,
                    "purged_eml_path": str(capture_path)}
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-delete-hard",
        args=args, drive_id=op.drive_id, item_id=op.item_id,
        before=audit_before,
    )

    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    try:
        graph.delete(f"{ub}/messages/{message_id}")
    except GraphError as e:
        log_mutation_end(
            logger, op_id=op.op_id, after={"purged_eml_path": str(capture_path)},
            result="error", error=str(e),
        )
        return HardDeleteResult(
            op_id=op.op_id, status="error", error=str(e),
            after={"purged_eml_path": str(capture_path)},
        )
    log_mutation_end(
        logger, op_id=op.op_id,
        after={"purged_eml_path": str(capture_path)}, result="ok",
    )
    return HardDeleteResult(
        op_id=op.op_id, status="ok",
        after={"purged_eml_path": str(capture_path)},
    )


def execute_empty_folder(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    purged_dir: Path,
    before: dict | None = None,
) -> HardDeleteResult:
    """Empty a named folder. Captures every message's EML before delete."""
    args = op.args
    mailbox_spec = args["mailbox_spec"]
    auth_mode = args["auth_mode"]
    folder_id = args["folder_id"]
    capture_root = _today_dir(purged_dir) / op.op_id

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-empty-folder",
        args=args, drive_id=op.drive_id, item_id=op.item_id,
        before=before or {},
    )

    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    list_path = f"{ub}/mailFolders/{folder_id}/messages"
    captured = 0
    failed: list[str] = []
    for items, _ in graph.get_paginated(
        list_path, params={"$select": "id", "$top": 100},
    ):
        for raw in items:
            mid = raw["id"]
            cap = capture_root / f"{mid}.eml"
            try:
                _capture_eml(
                    graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
                    message_id=mid, out_path=cap,
                )
            except GraphError as e:
                failed.append(f"{mid}: capture failed ({e})")
                continue
            try:
                graph.delete(f"{ub}/messages/{mid}")
                captured += 1
            except GraphError as e:
                failed.append(f"{mid}: delete failed ({e})")

    after = {
        "purged_count": captured,
        "purged_root": str(capture_root),
        "failures": failed,
    }
    if failed:
        log_mutation_end(
            logger, op_id=op.op_id, after=after, result="error",
            error=f"{len(failed)} per-message failures",
        )
        return HardDeleteResult(
            op_id=op.op_id, status="error",
            error="; ".join(failed[:5]) + ("…" if len(failed) > 5 else ""),
            after=after,
        )
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return HardDeleteResult(op_id=op.op_id, status="ok", after=after)


def execute_empty_recycle_bin(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    purged_dir: Path,
    before: dict | None = None,
) -> HardDeleteResult:
    """Empty Deleted Items. Wraps execute_empty_folder targeting ``deleteditems``."""
    # Force the well-known folder id.
    new_args = {**op.args, "folder_id": "deleteditems"}
    op2 = Operation(
        op_id=op.op_id, action="mail.empty.recycle-bin",
        drive_id=op.drive_id, item_id=op.item_id,
        args=new_args, dry_run_result=op.dry_run_result,
    )
    return execute_empty_folder(
        op2, graph, logger, purged_dir=purged_dir, before=before,
    )
