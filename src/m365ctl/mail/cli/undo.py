"""Mail-specific undo executor.

Dispatches mail.folder.* and mail.categories.* reverse-ops to the
corresponding execute_* functions in ``m365ctl.mail.mutate.{folders,categories}``.

Called from the top-level ``m365ctl.cli.undo`` router when the audit record's
``cmd`` field starts with ``"mail-"``.
"""
from __future__ import annotations

import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.auth import AppOnlyCredential, DelegatedCredential
from m365ctl.common.config import AuthMode, load_config
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.mail.folders import get_folder
from m365ctl.mail.mutate.folders import (
    execute_create_folder,
    execute_delete_folder,
    execute_move_folder,
    execute_rename_folder,
)
from m365ctl.mail.mutate.categories import (
    execute_add_category,
    execute_remove_category,
    execute_update_category,
)
from m365ctl.mail.mutate.undo import build_reverse_mail_operation
from m365ctl.onedrive.mutate.undo import Irreversible


def _mailbox_spec_from_drive_id(drive_id: str) -> str:
    """Drive-id holds the mailbox UPN (or "me"); reconstruct a mailbox spec."""
    return "me" if drive_id == "me" else f"upn:{drive_id}"


def _build_credential(cfg, auth_mode: AuthMode):
    return DelegatedCredential(cfg) if auth_mode == "delegated" else AppOnlyCredential(cfg)


def _lookup_folder_before(graph, *, mailbox_spec: str, auth_mode: AuthMode, folder_id: str) -> dict:
    """Fetch current folder state for a mail undo audit record. Best-effort.

    Returns a minimal dict suitable as ``before`` for an ``execute_*`` call.
    On Graph errors (404, auth), returns an empty dict — the audit log's
    ``before`` will be empty but the reverse mutation still executes.
    """
    try:
        f = get_folder(graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
                       folder_id=folder_id, path="(unknown)")
    except GraphError:
        return {}
    return {
        "display_name": f.display_name,
        "parent_id": f.parent_id,
        "path": f.path,
    }


def run_undo_mail(*, config_path: Path, op_id: str, confirm: bool) -> int:
    cfg = load_config(config_path)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)

    try:
        rev = build_reverse_mail_operation(logger, op_id)
    except Irreversible as e:
        print(f"irreversible: {e}", file=sys.stderr)
        return 2

    print(f"Reverse op: {rev.action} — {rev.dry_run_result}")
    if not confirm:
        print("DRY-RUN — pass --confirm to execute.")
        return 0

    # Reconstruct auth for the reverse op. Default to delegated; app-only would
    # have been set in the original op's args via CLI flow but audit log stores
    # auth_mode inside args.
    auth_mode_raw = rev.args.get("auth_mode", "delegated")
    auth_mode: AuthMode = (
        "app-only" if auth_mode_raw == "app-only" else "delegated"
    )
    cred = _build_credential(cfg, auth_mode)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    mailbox_spec = _mailbox_spec_from_drive_id(rev.drive_id)

    # Dispatch on action suffix.
    action = rev.action

    if action == "mail.folder.delete":
        # Look up current folder meta for before.
        before = _lookup_folder_before(
            graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
            folder_id=rev.item_id,
        )
        # Thread auth_mode into args so the executor routes correctly.
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_delete_folder(rev, graph, logger, before=before)
    elif action == "mail.folder.rename":
        before = _lookup_folder_before(
            graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
            folder_id=rev.item_id,
        )
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_rename_folder(rev, graph, logger, before=before)
    elif action == "mail.folder.move":
        before = _lookup_folder_before(
            graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
            folder_id=rev.item_id,
        )
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_move_folder(rev, graph, logger, before=before)
    elif action == "mail.folder.create":
        # Reverse of a delete — not reachable in Phase 2 (delete is irreversible).
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_create_folder(rev, graph, logger, before={})
    elif action == "mail.categories.add":
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_add_category(rev, graph, logger, before={})
    elif action == "mail.categories.update":
        # Fetch current state for before — list master categories and find by id.
        from m365ctl.mail.categories import list_master_categories
        current = list_master_categories(graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode)
        before_cat = next(
            ({"display_name": c.display_name, "color": c.color} for c in current if c.id == rev.item_id),
            {},
        )
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_update_category(rev, graph, logger, before=before_cat)
    elif action == "mail.categories.remove":
        from m365ctl.mail.categories import list_master_categories
        current = list_master_categories(graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode)
        before_cat = next(
            ({"display_name": c.display_name, "color": c.color} for c in current if c.id == rev.item_id),
            {},
        )
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_remove_category(rev, graph, logger, before=before_cat)
    elif action == "mail.move":
        from m365ctl.mail.mutate.move import execute_move
        rev.args.setdefault("auth_mode", auth_mode)
        try:
            from m365ctl.mail.messages import get_message
            msg = get_message(graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode, message_id=rev.item_id)
            current_before = {
                "parent_folder_id": msg.parent_folder_id,
                "parent_folder_path": msg.parent_folder_path,
            }
        except Exception:
            current_before = {}
        r = execute_move(rev, graph, logger, before=current_before)

    elif action == "mail.delete.soft":
        from m365ctl.mail.mutate.delete import execute_soft_delete
        rev.args.setdefault("auth_mode", auth_mode)
        # before capture for the undo of THIS undo (i.e. if user later re-deletes the
        # restored message): fetch current parent so the next audit record is useful.
        try:
            from m365ctl.mail.messages import get_message
            msg = get_message(
                graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
                message_id=rev.item_id,
            )
            current_before = {
                "parent_folder_id": msg.parent_folder_id,
                "parent_folder_path": msg.parent_folder_path,
            }
        except Exception:
            current_before = {}
        r = execute_soft_delete(rev, graph, logger, before=current_before)

    elif action == "mail.flag":
        from m365ctl.mail.mutate.flag import execute_flag
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_flag(rev, graph, logger, before={})

    elif action == "mail.read":
        from m365ctl.mail.mutate.read import execute_read
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_read(rev, graph, logger, before={})

    elif action == "mail.focus":
        from m365ctl.mail.mutate.focus import execute_focus
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_focus(rev, graph, logger, before={})

    elif action == "mail.categorize":
        from m365ctl.mail.mutate.categorize import execute_categorize
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_categorize(rev, graph, logger, before={})

    elif action == "mail.draft.create":
        from m365ctl.mail.mutate.draft import execute_create_draft
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_create_draft(rev, graph, logger, before={})

    elif action == "mail.draft.update":
        from m365ctl.mail.mutate.draft import execute_update_draft
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_update_draft(rev, graph, logger, before={})

    elif action == "mail.draft.delete":
        from m365ctl.mail.mutate.draft import execute_delete_draft
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_delete_draft(rev, graph, logger, before={})

    elif action == "mail.attach.add":
        from m365ctl.mail.mutate.attach import execute_add_attachment_small
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_add_attachment_small(rev, graph, logger, before={})

    elif action == "mail.attach.remove":
        from m365ctl.mail.mutate.attach import execute_remove_attachment
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_remove_attachment(rev, graph, logger, before={})
    else:
        print(f"no mail executor wired for reverse action {action!r}", file=sys.stderr)
        return 2

    if r.status != "ok":
        print(f"undo failed: {r.error}", file=sys.stderr)
        return 1
    print(f"[{rev.op_id}] ok (reverse of {op_id})")
    return 0
