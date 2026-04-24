"""Folder CRUD mutations — create, rename, move, delete (soft).

All four ``execute_*`` functions:
- Take ``(op, graph, logger, *, before)`` like ``onedrive.mutate.rename.execute_rename``.
- Emit ``log_mutation_start`` with the ``before`` block, call Graph, emit
  ``log_mutation_end`` with ``after``.
- Return a ``MailResult`` (see ``_common.py``).

``op.drive_id`` holds the mailbox UPN (or the literal ``"me"``). ``op.item_id``
holds the parent folder id (for create) or the target folder id (for
rename/move/delete). The CLI layer populates these via
``mail.folders.resolve_folder_path``.

``op.args["auth_mode"]`` distinguishes delegated vs app-only and selects
``/me`` vs ``/users/{upn}`` routing. Default: delegated.
"""
from __future__ import annotations

from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.mutate._common import MailResult


def _user_base(op: Operation) -> str:
    """Resolve the Graph URL prefix from ``op.drive_id`` + ``op.args``."""
    auth_mode = op.args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)


def execute_create_folder(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /mailFolders (or .../{parent}/childFolders) with {displayName}."""
    name = op.args["name"]
    parent_id = op.item_id
    parent_path = op.args.get("parent_path", "") or ""
    ub = _user_base(op)

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-folder-create",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        path = f"{ub}/mailFolders" if not parent_id else f"{ub}/mailFolders/{parent_id}/childFolders"
        created = graph.post(path, json={"displayName": name})
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))

    new_path = f"{parent_path}/{name}" if parent_path else name
    after: dict[str, Any] = {"id": created.get("id", ""), "path": new_path}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_rename_folder(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """PATCH /mailFolders/{id} with {displayName}."""
    new_name = op.args["new_name"]
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-folder-rename",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        result = graph.patch(
            f"{ub}/mailFolders/{op.item_id}",
            json_body={"displayName": new_name},
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"display_name": result.get("displayName", new_name)}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_move_folder(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /mailFolders/{id}/move with {destinationId}."""
    dest_id = op.args["destination_id"]
    dest_path = op.args.get("destination_path", "") or ""
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-folder-move",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.post(
            f"{ub}/mailFolders/{op.item_id}/move",
            json={"destinationId": dest_id},
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"parent_id": dest_id, "path": dest_path}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_delete_folder(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """DELETE /mailFolders/{id} (Graph moves it to Deleted Items — soft delete)."""
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-folder-delete",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.delete(f"{ub}/mailFolders/{op.item_id}")
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    log_mutation_end(logger, op_id=op.op_id, after=None, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=None)
