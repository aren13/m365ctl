"""`m365ctl mail categorize` — add/remove/set categories on a message."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, load_plan, new_op_id
from m365ctl.mail.cli._bulk import confirm_bulk_proceed
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.messages import get_message
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.categorize import execute_categorize


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail categorize")
    add_common_args(p)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--message-id")
    p.add_argument("--add", action="append", default=[],
                   help="Add category. Repeatable.")
    p.add_argument("--remove", action="append", default=[],
                   help="Remove category. Repeatable.")
    p.add_argument("--set", dest="set_", action="append", default=[],
                   help="Set exact category list. Repeatable. Mutually exclusive with add/remove.")
    p.add_argument("--from-plan")
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


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.from_plan:
        if not args.confirm:
            print("mail categorize --from-plan requires --confirm.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        plan = load_plan(Path(args.from_plan))
        ops = [op for op in plan.operations if op.action == "mail.categorize"]
        if not confirm_bulk_proceed(len(ops), verb="categorize"):
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
                before = {"categories": list(msg.categories)}
            except Exception:
                before = {}
            result = execute_categorize(op, graph, logger, before=before)
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    if not args.message_id:
        print("mail categorize: pass --message-id (or --from-plan --confirm).",
              file=sys.stderr)
        return 2
    if args.set_ and (args.add or args.remove):
        print("mail categorize: --set is mutually exclusive with --add/--remove.",
              file=sys.stderr)
        return 2
    if not (args.set_ or args.add or args.remove):
        print("mail categorize: pass --set, --add, or --remove.", file=sys.stderr)
        return 2

    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        print(f"(dry-run) would categorize {args.message_id}: set={args.set_}, add={args.add}, remove={args.remove}",
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
