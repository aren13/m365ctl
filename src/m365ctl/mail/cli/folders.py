"""`m365ctl mail folders [list|create|rename|move|delete]` — reader + CRUD."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.config import load_config
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.common.safety import ScopeViolation, is_folder_denied
from m365ctl.mail.cli._common import (
    add_common_args,
    emit_json_lines,
    load_and_authorize,
)
from m365ctl.mail.folders import FolderNotFound, list_folders, resolve_folder_path
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.folders import (
    execute_create_folder,
    execute_delete_folder,
    execute_move_folder,
    execute_rename_folder,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail folders")
    add_common_args(p)
    # Reader flags remain at the root for backwards compatibility with Phase 1.
    p.add_argument("--tree", action="store_true")
    p.add_argument("--with-counts", action="store_true")
    p.add_argument("--include-hidden", action="store_true")

    sub = p.add_subparsers(dest="subcommand", required=False)

    c = sub.add_parser("create", help="Create a child folder.")
    c.add_argument("parent_path", help="Parent folder path (use '' for root).")
    c.add_argument("name", help="New folder name.")
    c.add_argument("--confirm", action="store_true")

    r = sub.add_parser("rename", help="Rename a folder.")
    r.add_argument("path")
    r.add_argument("new_name")
    r.add_argument("--confirm", action="store_true")

    m = sub.add_parser("move", help="Move a folder under a new parent.")
    m.add_argument("path")
    m.add_argument("new_parent_path")
    m.add_argument("--confirm", action="store_true")

    d = sub.add_parser("delete", help="Delete a folder (soft delete).")
    d.add_argument("path")
    d.add_argument("--confirm", action="store_true")

    return p


# ---- reader (unchanged semantics from Phase 1) ----------------------------

def _run_list(args: argparse.Namespace) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    folders = list(list_folders(
        graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        include_hidden=args.include_hidden,
    ))
    folders = [f for f in folders if not is_folder_denied(f.path, cfg)]

    if args.json:
        emit_json_lines(folders)
        return 0

    if args.tree:
        for f in folders:
            depth = f.path.count("/")
            indent = "  " * depth
            counts = f"  ({f.total_items}/{f.unread_items})" if args.with_counts else ""
            print(f"{indent}{f.display_name}{counts}")
    else:
        for f in folders:
            counts = f"  ({f.total_items}/{f.unread_items})" if args.with_counts else ""
            print(f"{f.path}{counts}")
    return 0


# ---- mutations -------------------------------------------------------------

def _build_audit_logger(cfg) -> AuditLogger:
    return AuditLogger(ops_dir=cfg.logging.ops_dir)


def _preauth_deny_check(args: argparse.Namespace, *paths: str) -> None:
    """Load config cheaply and run deny-folder check BEFORE credential construction.

    The deny check must fire even when MSAL / cert setup would fail, so the
    CLI rejects attempts to touch denied folders immediately. Leading slashes
    are stripped to match ``list_folders`` path semantics (``"Inbox/Triage"``).
    """
    cfg = load_config(Path(args.config))
    for path in paths:
        normalized = path.strip("/")
        if is_folder_denied(normalized, cfg):
            raise ScopeViolation(
                f"folder {path!r} matches a deny pattern "
                f"(compliance or scope.deny_folders); mutation blocked"
            )


def _run_create(args: argparse.Namespace) -> int:
    # New folder path = parent_path + "/" + name; denial check covers both.
    parent_norm = args.parent_path.strip("/")
    new_path = f"{parent_norm}/{args.name}" if parent_norm else args.name
    _preauth_deny_check(args, new_path)
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope, folder_path=new_path,
    )
    if not args.confirm:
        print(f"(dry-run) would create folder {new_path!r}", file=sys.stderr)
        return 0

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    if args.parent_path:
        parent_id = resolve_folder_path(
            args.parent_path, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
    else:
        parent_id = ""

    op = Operation(
        op_id=new_op_id(),
        action="mail.folder.create",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id=parent_id,
        args={"name": args.name, "parent_path": args.parent_path, "auth_mode": auth_mode},
    )
    result = execute_create_folder(op, graph, _build_audit_logger(cfg), before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — created {new_path!r} (id: {(result.after or {}).get('id', '')})")
    return 0


def _run_rename(args: argparse.Namespace) -> int:
    _preauth_deny_check(args, args.path)
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope, folder_path=args.path,
    )
    if not args.confirm:
        print(f"(dry-run) would rename {args.path!r} -> {args.new_name!r}", file=sys.stderr)
        return 0

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        folder_id = resolve_folder_path(
            args.path, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
    except FolderNotFound as e:
        print(f"mail folders rename: {e}", file=sys.stderr)
        return 2

    op = Operation(
        op_id=new_op_id(),
        action="mail.folder.rename",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id=folder_id,
        args={"new_name": args.new_name, "auth_mode": auth_mode},
    )
    before = {"display_name": args.path.strip("/").split("/")[-1], "path": args.path}
    result = execute_rename_folder(op, graph, _build_audit_logger(cfg), before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — renamed {args.path!r} -> {args.new_name!r}")
    return 0


def _run_move(args: argparse.Namespace) -> int:
    _preauth_deny_check(args, args.path, args.new_parent_path)
    cfg, auth_mode, cred = load_and_authorize(args)
    # BOTH source and destination must pass the deny-folder check.
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope, folder_path=args.path,
    )
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope, folder_path=args.new_parent_path,
    )
    if not args.confirm:
        print(
            f"(dry-run) would move {args.path!r} -> {args.new_parent_path!r}",
            file=sys.stderr,
        )
        return 0

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        folder_id = resolve_folder_path(
            args.path, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
        # Root sentinels: "", "/", "root", "msgfolderroot" all map to the
        # well-known mailbox root folder. Graph accepts "msgfolderroot" as
        # a destinationId directly without a path-resolution step.
        if args.new_parent_path in ("", "/", "root", "msgfolderroot"):
            dest_id = "msgfolderroot"
        else:
            dest_id = resolve_folder_path(
                args.new_parent_path, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
            )
    except FolderNotFound as e:
        print(f"mail folders move: {e}", file=sys.stderr)
        return 2

    parent_path = "/".join(args.path.strip("/").split("/")[:-1])
    parent_id = ""
    if parent_path:
        try:
            parent_id = resolve_folder_path(
                parent_path, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
            )
        except FolderNotFound:
            parent_id = ""

    op = Operation(
        op_id=new_op_id(),
        action="mail.folder.move",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id=folder_id,
        args={"destination_id": dest_id, "destination_path": args.new_parent_path,
              "auth_mode": auth_mode},
    )
    before = {"parent_id": parent_id, "path": args.path}
    result = execute_move_folder(op, graph, _build_audit_logger(cfg), before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — moved {args.path!r} -> {args.new_parent_path!r}")
    return 0


def _run_delete(args: argparse.Namespace) -> int:
    _preauth_deny_check(args, args.path)
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope, folder_path=args.path,
    )
    if not args.confirm:
        print(f"(dry-run) would delete folder {args.path!r}", file=sys.stderr)
        return 0

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        folder_id = resolve_folder_path(
            args.path, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
    except FolderNotFound as e:
        print(f"mail folders delete: {e}", file=sys.stderr)
        return 2

    op = Operation(
        op_id=new_op_id(),
        action="mail.folder.delete",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id=folder_id,
        args={"auth_mode": auth_mode},
    )
    before = {"id": folder_id, "display_name": args.path.strip("/").split("/")[-1],
              "path": args.path}
    result = execute_delete_folder(op, graph, _build_audit_logger(cfg), before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — deleted folder {args.path!r}")
    return 0


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.subcommand is None:
        return _run_list(args)
    if args.subcommand == "create":
        return _run_create(args)
    if args.subcommand == "rename":
        return _run_rename(args)
    if args.subcommand == "move":
        return _run_move(args)
    if args.subcommand == "delete":
        return _run_delete(args)
    return 2
