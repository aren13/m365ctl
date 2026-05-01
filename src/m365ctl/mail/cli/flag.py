"""`m365ctl mail flag` — set/clear the flag on one or more messages."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, load_plan, new_op_id
from m365ctl.mail.cli._bulk import confirm_bulk_proceed, execute_plan_in_batches
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.messages import get_message
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.flag import execute_flag, finish_flag, start_flag


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail flag")
    add_common_args(p)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--message-id")
    p.add_argument("--status", choices=("notFlagged", "flagged", "complete"))
    p.add_argument("--start")
    p.add_argument("--due")
    p.add_argument("--from-plan")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.from_plan:
        if not args.confirm:
            print("mail flag --from-plan requires --confirm.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        plan = load_plan(Path(args.from_plan))
        ops = [op for op in plan.operations if op.action == "mail.flag"]
        if not confirm_bulk_proceed(len(ops), verb="flag"):
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
            start_op=start_flag, finish_op=finish_flag,
            on_result=on_result,
        )

    if not args.message_id or not args.status:
        print("mail flag: pass --message-id + --status (or --from-plan --confirm).",
              file=sys.stderr)
        return 2
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        print(f"(dry-run) would flag {args.message_id} status={args.status}",
              file=sys.stderr)
        return 0
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                          message_id=args.message_id)
        before = {
            "status": msg.flag.status,
            "start_at": msg.flag.start_at.isoformat() if msg.flag.start_at else None,
            "due_at": msg.flag.due_at.isoformat() if msg.flag.due_at else None,
        }
    except Exception:
        before = {}
    op = Operation(
        op_id=new_op_id(), action="mail.flag",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
        args={"status": args.status,
              "start_at": args.start,
              "due_at": args.due,
              "auth_mode": auth_mode},
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_flag(op, graph, logger, before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — flagged {args.message_id} status={args.status}")
    return 0
