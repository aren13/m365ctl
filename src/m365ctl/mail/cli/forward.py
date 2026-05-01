"""`m365ctl mail forward` — forward a message (create draft OR inline send)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.forward import execute_create_forward, execute_send_forward_inline


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail forward")
    add_common_args(p)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("message_id")
    p.add_argument("--inline", action="store_true")
    p.add_argument("--body")
    p.add_argument("--body-file")
    p.add_argument("--to", action="append", default=[])
    return p


def _read_body(args) -> str:
    if args.body_file:
        return Path(args.body_file).read_text()
    return args.body or ""


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
        assume_yes=getattr(args, "assume_yes", False),
    )
    if not args.confirm:
        mode = "inline" if args.inline else "create draft"
        print(f"(dry-run) would forward ({mode}) {args.message_id} to {args.to}",
              file=sys.stderr)
        return 0

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)

    if args.inline:
        if not args.to:
            print("mail forward --inline requires at least one --to.", file=sys.stderr)
            return 2
        body = _read_body(args)
        op = Operation(
            op_id=new_op_id(), action="mail.forward",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
            args={"mode": "inline", "body": body, "to": list(args.to),
                  "auth_mode": auth_mode},
        )
        result = execute_send_forward_inline(op, graph, logger, before={})
    else:
        op = Operation(
            op_id=new_op_id(), action="mail.forward",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
            args={"mode": "create", "auth_mode": auth_mode},
        )
        result = execute_create_forward(op, graph, logger, before={})

    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    if args.inline:
        print(f"[{op.op_id}] ok — forwarded inline")
    else:
        new_draft = (result.after or {}).get("draft_id", "")
        print(f"[{op.op_id}] ok — created forward-draft {new_draft}")
    return 0
