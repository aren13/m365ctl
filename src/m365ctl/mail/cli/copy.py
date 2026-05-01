"""`m365ctl mail copy` — copy one or more messages to a destination folder.

Three modes:
1. Single-item: `--message-id <id> --to-folder <path>` + `--confirm`.
2. Bulk dry-run: filter flags + `--to-folder <path>` + `--plan-out <file>`.
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
    execute_plan_in_batches,
    expand_messages_for_pattern,
)
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.folders import FolderNotFound, resolve_folder_path
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.copy import execute_copy, finish_copy, start_copy


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail copy")
    add_common_args(p)

    # Mode 1: single-item
    p.add_argument("--message-id", help="Copy one specific message.")

    # Mode 2: bulk pattern — filters inherited from mail list
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

    # Destination + plan plumbing
    p.add_argument("--to-folder", help="Destination folder path.")
    p.add_argument("--plan-out", help="Write plan to this path and exit (dry run).")
    p.add_argument("--from-plan", help="Execute ops from this plan file (requires --confirm).")

    p.add_argument("--confirm", action="store_true",
                   help="Actually execute (otherwise dry-run).")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--page-size", type=int, default=50)
    return p


def _build_filter(args) -> MessageFilter:
    unread_flag: bool | None = None
    if args.unread and args.read:
        return MessageFilter()
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
            print("mail copy --from-plan requires --confirm.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        plan = load_plan(Path(args.from_plan))
        ops = [op for op in plan.operations if op.action == "mail.copy"]
        if not ops:
            print("mail copy --from-plan: no mail.copy ops in plan.", file=sys.stderr)
            return 2
        if not confirm_bulk_proceed(len(ops), verb="copy", assume_yes=getattr(args, "assume_yes", False)):
            print("aborted: user declined /dev/tty confirm.", file=sys.stderr)
            return 2
        for op in ops:
            op.args.setdefault("auth_mode", auth_mode)
        token = cred.get_token()
        graph = GraphClient(token_provider=lambda: token)
        logger = AuditLogger(ops_dir=cfg.logging.ops_dir)

        def on_result(op, result):
            if result.status == "ok":
                print(f"[{op.op_id}] ok")
            else:
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)

        return execute_plan_in_batches(
            graph=graph, logger=logger, ops=ops,
            fetch_before=None, parse_before=lambda *_: {},
            start_op=start_copy, finish_op=finish_copy,
            on_result=on_result,
        )

    # --- Single-item mode ---------------------------------------------------
    if args.message_id:
        if not args.to_folder:
            print("mail copy --message-id requires --to-folder.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        assert_mail_target_allowed(
            cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
            unsafe_scope=args.unsafe_scope, folder_path=args.to_folder,
            assume_yes=getattr(args, "assume_yes", False),
        )
        if not args.confirm:
            print(f"(dry-run) would copy {args.message_id} -> {args.to_folder!r}",
                  file=sys.stderr)
            return 0
        token = cred.get_token()
        graph = GraphClient(token_provider=lambda: token)
        try:
            dest_id = resolve_folder_path(
                args.to_folder, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
            )
        except FolderNotFound as e:
            print(f"mail copy: {e}", file=sys.stderr)
            return 2
        op = Operation(
            op_id=new_op_id(), action="mail.copy",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
            args={"destination_id": dest_id, "destination_path": args.to_folder,
                  "auth_mode": auth_mode},
        )
        logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
        result = execute_copy(op, graph, logger, before={})
        if result.status != "ok":
            print(f"error: {result.error}", file=sys.stderr)
            return 1
        print(f"[{op.op_id}] ok — copied {args.message_id} -> {args.to_folder!r}")
        return 0

    # --- Bulk plan-out mode -------------------------------------------------
    if not args.to_folder:
        print("mail copy: pass --message-id, --from-plan, or filter flags with --to-folder.",
              file=sys.stderr)
        return 2
    if args.unread and args.read:
        print("mail copy: --unread and --read are mutually exclusive", file=sys.stderr)
        return 2

    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope, folder_path=args.to_folder,
        assume_yes=getattr(args, "assume_yes", False),
    )
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        source_folder_id = resolve_folder_path(
            args.folder, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
        dest_id = resolve_folder_path(
            args.to_folder, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
    except FolderNotFound as e:
        print(f"mail copy: {e}", file=sys.stderr)
        return 2

    msgs = list(expand_messages_for_pattern(
        graph=graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        resolved_folders=[(source_folder_id, args.folder)],
        filter=_build_filter(args),
        limit=args.limit, page_size=args.page_size,
    ))
    if not msgs:
        print("mail copy: no matching messages; nothing to do.")
        return 0

    ops = [
        Operation(
            op_id=new_op_id(), action="mail.copy",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=m.id,
            args={"destination_id": dest_id, "destination_path": args.to_folder,
                  "auth_mode": auth_mode},
            dry_run_result=f"would copy {m.id} ({m.subject!r}) -> {args.to_folder}",
        )
        for m in msgs
    ]

    if args.plan_out:
        emit_plan(
            Path(args.plan_out),
            source_cmd=f"mail copy --from {args.from_address or '?'} --to-folder {args.to_folder}",
            scope=derive_mailbox_upn(args.mailbox),
            operations=ops,
        )
        print(f"Wrote plan with {len(ops)} operations to {args.plan_out}.")
        print(f"Review, then: mail copy --from-plan {args.plan_out} --confirm")
        return 0

    print(f"mail copy: matched {len(msgs)} messages. Pass --plan-out <path> to persist, "
          f"or --confirm to execute inline.")
    for op in ops[:10]:
        print(f"  {op.dry_run_result}")
    if len(ops) > 10:
        print(f"  ... and {len(ops) - 10} more")
    return 0
