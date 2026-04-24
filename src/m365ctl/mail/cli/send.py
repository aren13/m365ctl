"""`m365ctl mail send` — send an existing draft OR send inline."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, load_plan, new_op_id
from m365ctl.mail.cli._bulk import confirm_bulk_proceed
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.compose import count_external_recipients, parse_recipients
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.send import execute_send_draft, execute_send_new


_EXTERNAL_RECIP_TTY_THRESHOLD = 20


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="m365ctl mail send",
        description="Send an existing draft (by id) OR, if --new, send inline. "
                    "`--new` is blocked when [mail].drafts_before_send is true (default).",
    )
    add_common_args(p)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("draft_id", nargs="?", help="Draft id to send.")
    p.add_argument("--new", action="store_true",
                   help="Send inline (no persistent draft). Blocked when drafts_before_send=true.")
    p.add_argument("--subject")
    p.add_argument("--body")
    p.add_argument("--body-file")
    p.add_argument("--body-type", choices=("text", "html"), default="text")
    p.add_argument("--to", action="append", default=[])
    p.add_argument("--cc", action="append", default=[])
    p.add_argument("--bcc", action="append", default=[])
    p.add_argument("--importance", choices=("low", "normal", "high"))
    p.add_argument("--from-plan")
    return p


def _read_body(args) -> str:
    if args.body_file:
        return Path(args.body_file).read_text()
    return args.body or ""


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.from_plan:
        if not args.confirm:
            print("mail send --from-plan requires --confirm.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        plan = load_plan(Path(args.from_plan))
        ops = [op for op in plan.operations if op.action == "mail.send"]
        if not confirm_bulk_proceed(len(ops), verb="send"):
            return 2
        token = cred.get_token()
        graph = GraphClient(token_provider=lambda: token)
        logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
        any_error = False
        for op in ops:
            op.args.setdefault("auth_mode", auth_mode)
            if op.args.get("new"):
                result = execute_send_new(op, graph, logger, before={})
            else:
                result = execute_send_draft(op, graph, logger, before={})
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    if args.new:
        cfg, auth_mode, cred = load_and_authorize(args)
        if cfg.mail.drafts_before_send:
            print(
                "mail send --new: blocked by [mail].drafts_before_send=true. "
                "Use `mail draft create` + `mail send <draft-id>`, or set "
                "[mail].drafts_before_send=false in config.toml.",
                file=sys.stderr,
            )
            return 2
        assert_mail_target_allowed(
            cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
            unsafe_scope=args.unsafe_scope,
        )
        recips = parse_recipients(args.to + args.cc + args.bcc)
        external = count_external_recipients(recips, internal_domain=None)
        if external > _EXTERNAL_RECIP_TTY_THRESHOLD:
            from m365ctl.common.safety import _confirm_via_tty
            prompt = f"mail send: {external} external recipients. Proceed? [y/N]: "
            if not _confirm_via_tty(prompt):
                print("aborted: user declined /dev/tty confirm.", file=sys.stderr)
                return 2
        if not args.confirm:
            print(f"(dry-run) would send inline to={args.to} subject={args.subject!r}",
                  file=sys.stderr)
            return 0
        body = _read_body(args)
        token = cred.get_token()
        graph = GraphClient(token_provider=lambda: token)
        op = Operation(
            op_id=new_op_id(), action="mail.send",
            drive_id=derive_mailbox_upn(args.mailbox), item_id="",
            args={
                "subject": args.subject or "",
                "body": body,
                "body_type": args.body_type,
                "to": list(args.to),
                "cc": list(args.cc),
                "bcc": list(args.bcc),
                "importance": args.importance,
                "new": True,
                "auth_mode": auth_mode,
            },
        )
        logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
        result = execute_send_new(op, graph, logger, before={})
        if result.status != "ok":
            print(f"error: {result.error}", file=sys.stderr)
            return 1
        print(f"[{op.op_id}] ok — sent")
        return 0

    if not args.draft_id:
        print("mail send: pass draft_id (or --new, or --from-plan --confirm).", file=sys.stderr)
        return 2
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        print(f"(dry-run) would send draft {args.draft_id}", file=sys.stderr)
        return 0
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    op = Operation(
        op_id=new_op_id(), action="mail.send",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.draft_id,
        args={"auth_mode": auth_mode},
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_send_draft(op, graph, logger, before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — sent draft {args.draft_id}")
    return 0
