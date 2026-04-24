"""`m365ctl mail focus` — set inferenceClassification (focused | other)."""
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
from m365ctl.mail.mutate.focus import execute_focus


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail focus")
    add_common_args(p)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--message-id")
    p.add_argument("--focused", dest="classification",
                   action="store_const", const="focused")
    p.add_argument("--other", dest="classification",
                   action="store_const", const="other")
    p.add_argument("--from-plan")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.from_plan:
        if not args.confirm:
            print("mail focus --from-plan requires --confirm.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        plan = load_plan(Path(args.from_plan))
        ops = [op for op in plan.operations if op.action == "mail.focus"]
        if not confirm_bulk_proceed(len(ops), verb="focus"):
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
                before = {"inference_classification": msg.inference_classification}
            except Exception:
                before = {}
            result = execute_focus(op, graph, logger, before=before)
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    if not args.message_id or args.classification is None:
        print("mail focus: pass --message-id + --focused or --other (or --from-plan --confirm).",
              file=sys.stderr)
        return 2
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        print(f"(dry-run) would set focus={args.classification} on {args.message_id}",
              file=sys.stderr)
        return 0
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                          message_id=args.message_id)
        before = {"inference_classification": msg.inference_classification}
    except Exception:
        before = {}
    op = Operation(
        op_id=new_op_id(), action="mail.focus",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
        args={"inference_classification": args.classification, "auth_mode": auth_mode},
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_focus(op, graph, logger, before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — set focus={args.classification} on {args.message_id}")
    return 0
