"""`m365ctl mail delete` — soft-delete one or more messages (→ Deleted Items).

This is the SOFT delete: messages move to the Deleted Items folder and can
be restored via ``m365ctl undo``. For hard/permanent delete see
``m365ctl mail clean`` (arrives Phase 6).

Three modes:
1. Single-item: `--message-id <id> --confirm`.
2. Bulk dry-run: filter flags + `--plan-out <file>`.
3. Bulk execute: `--from-plan <file> --confirm`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, load_plan, new_op_id
from m365ctl.mail.cli._bulk import (
    MessageFilter,
    confirm_bulk_proceed,
    emit_plan,
    expand_messages_for_pattern,
)
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.folders import FolderNotFound, resolve_folder_path
from m365ctl.mail.messages import get_message
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.delete import execute_soft_delete


_DESCRIPTION = (
    "Soft-delete messages (move to Deleted Items). "
    "For hard/permanent delete see `mail clean` (arrives Phase 6). "
    "All soft-deletes are reversible via `m365ctl undo <op-id>`."
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail delete", description=_DESCRIPTION)
    add_common_args(p)
    p.add_argument("--confirm", action="store_true")

    p.add_argument("--message-id", help="Soft-delete one specific message.")

    p.add_argument("--folder", default="Inbox",
                   help="Source folder (default: Inbox). Used in bulk mode.")
    p.add_argument("--from", dest="from_address",
                   help="Filter by sender address.")
    p.add_argument("--subject", dest="subject_contains",
                   help="Filter by substring in subject.")
    p.add_argument("--since", help="ISO-8601 lower bound on receivedDateTime.")
    p.add_argument("--until", help="ISO-8601 upper bound on receivedDateTime.")
    p.add_argument("--unread", action="store_true")
    p.add_argument("--read", action="store_true")
    p.add_argument("--has-attachments", action="store_true")
    p.add_argument("--importance", choices=("low", "normal", "high"))
    p.add_argument("--focus", choices=("focused", "other"))
    p.add_argument("--category")

    p.add_argument("--plan-out", help="Write plan to this path and exit (dry run).")
    p.add_argument("--from-plan", help="Execute ops from this plan file (requires --confirm).")

    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--page-size", type=int, default=50)
    return p


def _build_filter(args) -> MessageFilter:
    if args.unread and args.read:
        return MessageFilter()
    unread_flag: bool | None = None
    if args.unread:
        unread_flag = True
    elif args.read:
        unread_flag = False
    return MessageFilter(
        unread=unread_flag,
        from_address=args.from_address,
        subject_contains=args.subject_contains,
        since=args.since,
        until=args.until,
        has_attachments=True if args.has_attachments else None,
        importance=args.importance,
        focus=args.focus,
        category=args.category,
    )


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    # --- From-plan mode (bulk execute) --------------------------------------
    if args.from_plan:
        if not args.confirm:
            print("mail delete --from-plan requires --confirm.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        plan = load_plan(Path(args.from_plan))
        ops = [op for op in plan.operations if op.action == "mail.delete.soft"]
        if not ops:
            print("mail delete --from-plan: no mail.delete.soft ops in plan.", file=sys.stderr)
            return 2
        if not confirm_bulk_proceed(len(ops), verb="delete"):
            print("aborted: user declined /dev/tty confirm.", file=sys.stderr)
            return 2
        token = cred.get_token()
        graph = GraphClient(token_provider=lambda: token)
        logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
        any_error = False
        for op in ops:
            op.args.setdefault("auth_mode", auth_mode)
            try:
                msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                                  message_id=op.item_id)
                before = {"parent_folder_id": msg.parent_folder_id,
                          "parent_folder_path": msg.parent_folder_path}
            except Exception:
                before = {}
            result = execute_soft_delete(op, graph, logger, before=before)
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    # --- Single-item mode ---------------------------------------------------
    if args.message_id:
        cfg, auth_mode, cred = load_and_authorize(args)
        assert_mail_target_allowed(
            cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
            unsafe_scope=args.unsafe_scope,
        )
        if not args.confirm:
            print(f"(dry-run) would soft-delete {args.message_id} (→ Deleted Items)",
                  file=sys.stderr)
            return 0
        token = cred.get_token()
        graph = GraphClient(token_provider=lambda: token)
        try:
            msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                              message_id=args.message_id)
            before = {"parent_folder_id": msg.parent_folder_id,
                      "parent_folder_path": msg.parent_folder_path}
        except Exception:
            before = {}
        op = Operation(
            op_id=new_op_id(), action="mail.delete.soft",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
            args={"auth_mode": auth_mode},
        )
        logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
        result = execute_soft_delete(op, graph, logger, before=before)
        if result.status != "ok":
            print(f"error: {result.error}", file=sys.stderr)
            return 1
        print(f"[{op.op_id}] ok — soft-deleted {args.message_id} (→ Deleted Items)")
        return 0

    # --- Bulk plan-out mode -------------------------------------------------
    if args.unread and args.read:
        print("mail delete: --unread and --read are mutually exclusive", file=sys.stderr)
        return 2

    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope, folder_path=args.folder,
    )
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        source_folder_id = resolve_folder_path(
            args.folder, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
    except FolderNotFound as e:
        print(f"mail delete: {e}", file=sys.stderr)
        return 2

    msgs = list(expand_messages_for_pattern(
        graph=graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        resolved_folders=[(source_folder_id, args.folder)],
        filter=_build_filter(args),
        limit=args.limit, page_size=args.page_size,
    ))
    if not msgs:
        print("mail delete: no matching messages; nothing to do.")
        return 0

    ops = [
        Operation(
            op_id=new_op_id(), action="mail.delete.soft",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=m.id,
            args={"auth_mode": auth_mode},
            dry_run_result=f"would soft-delete {m.id} ({m.subject!r})",
        )
        for m in msgs
    ]

    if args.plan_out:
        emit_plan(
            Path(args.plan_out),
            source_cmd=f"mail delete --from {args.from_address or '?'} --folder {args.folder}",
            scope=derive_mailbox_upn(args.mailbox),
            operations=ops,
        )
        print(f"Wrote plan with {len(ops)} soft-delete ops to {args.plan_out}.")
        print(f"Review, then: mail delete --from-plan {args.plan_out} --confirm")
        return 0

    print(f"mail delete: matched {len(msgs)} messages. Pass --plan-out <path> to persist, "
          f"then --from-plan <path> --confirm to execute.")
    for op in ops[:10]:
        print(f"  {op.dry_run_result}")
    if len(ops) > 10:
        print(f"  ... and {len(ops) - 10} more")
    return 0
