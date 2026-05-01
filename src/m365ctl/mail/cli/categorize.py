"""`m365ctl mail categorize` — add/remove/set categories on messages.

Three modes (mirrors `mail move`):
1. Single-item: ``--message-id <id>`` + ``--add``/``--remove``/``--set`` + ``--confirm``.
2. Bulk dry-run: filter flags + ``--add``/``--remove``/``--set`` + ``--plan-out <file>``.
3. Bulk execute: ``--from-plan <file> --confirm``.
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
from m365ctl.mail.endpoints import user_base_for_op
from m365ctl.mail.folders import FolderNotFound, resolve_folder_path
from m365ctl.mail.messages import get_message
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.categorize import (
    execute_categorize,
    finish_categorize,
    start_categorize,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail categorize")
    add_common_args(p)
    p.add_argument("--confirm", action="store_true")

    # Mode 1: single-item
    p.add_argument("--message-id")

    # Category deltas
    p.add_argument("--add", action="append", default=[],
                   help="Add category. Repeatable.")
    p.add_argument("--remove", action="append", default=[],
                   help="Remove category. Repeatable.")
    p.add_argument("--set", dest="set_", action="append", default=[],
                   help="Set exact category list. Repeatable. Mutually exclusive with add/remove.")

    # Mode 2: bulk pattern — filter flags mirror `mail move`.
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
    p.add_argument("--category",
                   help="Filter to messages already tagged with this category.")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--page-size", type=int, default=50)

    # Plan plumbing
    p.add_argument("--plan-out", help="Write plan to this path and exit (dry run).")
    p.add_argument("--from-plan", help="Execute ops from this plan file (requires --confirm).")
    return p


def _resolve_final_categories(current: list[str], add: list[str], remove: list[str], set_: list[str]) -> list[str]:
    if set_:
        return list(set_)
    out = list(current)
    for c in add:
        if c not in out:
            out.append(c)
    for c in remove:
        if c in out:
            out.remove(c)
    return out


def _build_filter(args: argparse.Namespace) -> MessageFilter:
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

    # --- From-plan mode (bulk execute, batched) -----------------------------
    if args.from_plan:
        if not args.confirm:
            print("mail categorize --from-plan requires --confirm.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        plan = load_plan(Path(args.from_plan))
        ops = [op for op in plan.operations if op.action == "mail.categorize"]
        if not ops:
            print("mail categorize --from-plan: no mail.categorize ops in plan.",
                  file=sys.stderr)
            return 2
        if not confirm_bulk_proceed(len(ops), verb="categorize", assume_yes=getattr(args, "assume_yes", False)):
            print("aborted: user declined /dev/tty confirm.", file=sys.stderr)
            return 2
        for op in ops:
            op.args.setdefault("auth_mode", auth_mode)
        token = cred.get_token()
        graph = GraphClient(token_provider=lambda: token)
        logger = AuditLogger(ops_dir=cfg.logging.ops_dir)

        def fetch_before(b, op):
            ub = user_base_for_op(op)
            return b.get(f"{ub}/messages/{op.item_id}?$select=id,categories")

        def parse_before(op, body, err):
            if not body:
                return {}
            return {"categories": list(body.get("categories", []))}

        def on_result(op, result):
            if result.status == "ok":
                print(f"[{op.op_id}] ok")
            else:
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)

        return execute_plan_in_batches(
            graph=graph, logger=logger, ops=ops,
            fetch_before=fetch_before, parse_before=parse_before,
            start_op=start_categorize, finish_op=finish_categorize,
            on_result=on_result,
        )

    # Validate delta flags up-front (shared by single & bulk modes).
    if args.set_ and (args.add or args.remove):
        print("mail categorize: --set is mutually exclusive with --add/--remove.",
              file=sys.stderr)
        return 2
    if not (args.set_ or args.add or args.remove):
        print("mail categorize: pass --set, --add, or --remove.", file=sys.stderr)
        return 2

    # --- Single-item mode ---------------------------------------------------
    if args.message_id:
        cfg, auth_mode, cred = load_and_authorize(args)
        assert_mail_target_allowed(
            cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
            unsafe_scope=args.unsafe_scope,
            assume_yes=getattr(args, "assume_yes", False),
        )
        if not args.confirm:
            print(f"(dry-run) would categorize {args.message_id}: "
                  f"set={args.set_}, add={args.add}, remove={args.remove}",
                  file=sys.stderr)
            return 0
        token = cred.get_token()
        graph = GraphClient(token_provider=lambda: token)
        try:
            msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                              message_id=args.message_id)
            current = list(msg.categories)
            before = {"categories": current}
        except Exception:
            current = []
            before = {}

        final = _resolve_final_categories(current, args.add, args.remove, args.set_)
        op = Operation(
            op_id=new_op_id(), action="mail.categorize",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
            args={"categories": final, "auth_mode": auth_mode},
        )
        logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
        result = execute_categorize(op, graph, logger, before=before)
        if result.status != "ok":
            print(f"error: {result.error}", file=sys.stderr)
            return 1
        print(f"[{op.op_id}] ok — categorized {args.message_id} {final}")
        return 0

    # --- Bulk plan-out mode -------------------------------------------------
    if args.unread and args.read:
        print("mail categorize: --unread and --read are mutually exclusive",
              file=sys.stderr)
        return 2

    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
        assume_yes=getattr(args, "assume_yes", False),
    )
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        source_folder_id = resolve_folder_path(
            args.folder, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
    except FolderNotFound as e:
        print(f"mail categorize: {e}", file=sys.stderr)
        return 2

    msgs = list(expand_messages_for_pattern(
        graph=graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        resolved_folders=[(source_folder_id, args.folder)],
        filter=_build_filter(args),
        limit=args.limit, page_size=args.page_size,
    ))
    if not msgs:
        print("mail categorize: no matching messages; nothing to do.")
        return 0

    ops = [
        Operation(
            op_id=new_op_id(), action="mail.categorize",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=m.id,
            args={
                "categories": _resolve_final_categories(
                    list(m.categories), args.add, args.remove, args.set_,
                ),
                "auth_mode": auth_mode,
            },
            dry_run_result=(
                f"would categorize {m.id} ({m.subject!r}) -> "
                f"{_resolve_final_categories(list(m.categories), args.add, args.remove, args.set_)}"
            ),
        )
        for m in msgs
    ]

    if args.plan_out:
        verb = "set" if args.set_ else ("add" if args.add else "remove")
        emit_plan(
            Path(args.plan_out),
            source_cmd=f"mail categorize --{verb} ... --folder {args.folder}",
            scope=derive_mailbox_upn(args.mailbox),
            operations=ops,
        )
        print(f"Wrote plan with {len(ops)} operations to {args.plan_out}.")
        print(f"Review, then: mail categorize --from-plan {args.plan_out} --confirm")
        return 0

    print(f"mail categorize: matched {len(msgs)} messages. Pass --plan-out <path> "
          f"to persist, or --confirm to execute inline.")
    for op in ops[:10]:
        print(f"  {op.dry_run_result}")
    if len(ops) > 10:
        print(f"  ... and {len(ops) - 10} more")
    return 0
