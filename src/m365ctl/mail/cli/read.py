"""`m365ctl mail read` — mark message read/unread."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, load_plan, new_op_id
from m365ctl.mail.cli._bulk import confirm_bulk_proceed, execute_plan_in_batches
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.endpoints import user_base_for_op
from m365ctl.mail.messages import get_message
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.read import execute_read, finish_read, start_read


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail read")
    add_common_args(p)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--message-id")
    p.add_argument("--yes", dest="set_read", action="store_const", const=True,
                   help="Mark message as read.")
    p.add_argument("--no", dest="set_read", action="store_const", const=False,
                   help="Mark message as unread.")
    p.add_argument("--from-plan")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.from_plan:
        if not args.confirm:
            print("mail read --from-plan requires --confirm.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        plan = load_plan(Path(args.from_plan))
        ops = [op for op in plan.operations if op.action == "mail.read"]
        if not confirm_bulk_proceed(len(ops), verb="read", assume_yes=getattr(args, "assume_yes", False)):
            return 2
        for op in ops:
            op.args.setdefault("auth_mode", auth_mode)
        token = cred.get_token()
        graph = GraphClient(token_provider=lambda: token)
        logger = AuditLogger(ops_dir=cfg.logging.ops_dir)

        def fetch_before(b, op):
            ub = user_base_for_op(op)
            return b.get(f"{ub}/messages/{op.item_id}?$select=id,isRead")

        def parse_before(op, body, err):
            if not body:
                return {}
            return {"is_read": bool(body.get("isRead", False))}

        def on_result(op, result):
            if result.status == "ok":
                print(f"[{op.op_id}] ok")
            else:
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)

        return execute_plan_in_batches(
            graph=graph, logger=logger, ops=ops,
            fetch_before=fetch_before, parse_before=parse_before,
            start_op=start_read, finish_op=finish_read,
            on_result=on_result,
        )

    if not args.message_id or args.set_read is None:
        print("mail read: pass --message-id + --yes or --no (or --from-plan --confirm).",
              file=sys.stderr)
        return 2
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
        assume_yes=getattr(args, "assume_yes", False),
    )
    if not args.confirm:
        print(f"(dry-run) would set is_read={args.set_read} on {args.message_id}",
              file=sys.stderr)
        return 0
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                          message_id=args.message_id)
        before = {"is_read": msg.is_read}
    except Exception:
        before = {}
    op = Operation(
        op_id=new_op_id(), action="mail.read",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
        args={"is_read": args.set_read, "auth_mode": auth_mode},
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_read(op, graph, logger, before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    state = "read" if args.set_read else "unread"
    print(f"[{op.op_id}] ok — marked {args.message_id} as {state}")
    return 0
