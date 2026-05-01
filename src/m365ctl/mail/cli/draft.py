"""`m365ctl mail draft {create|update|delete}` — draft lifecycle."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.draft import (
    execute_create_draft,
    execute_delete_draft,
    execute_update_draft,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail draft")
    add_common_args(p)
    p.add_argument("--confirm", action="store_true")
    sub = p.add_subparsers(dest="subcommand", required=True)

    c = sub.add_parser("create", help="Create a new draft.")
    c.add_argument("--subject", default="")
    c.add_argument("--body", help="Body text (inline). Prefer --body-file.")
    c.add_argument("--body-file", help="Path to body content (text or HTML).")
    c.add_argument("--body-type", choices=("text", "html"), default="text")
    c.add_argument("--to", action="append", default=[])
    c.add_argument("--cc", action="append", default=[])
    c.add_argument("--bcc", action="append", default=[])
    c.add_argument("--importance", choices=("low", "normal", "high"))
    c.add_argument("--confirm", action="store_true")

    u = sub.add_parser("update", help="Update an existing draft.")
    u.add_argument("draft_id")
    u.add_argument("--subject")
    u.add_argument("--body")
    u.add_argument("--body-file")
    u.add_argument("--body-type", choices=("text", "html"))
    u.add_argument("--to", action="append", default=[])
    u.add_argument("--cc", action="append", default=[])
    u.add_argument("--bcc", action="append", default=[])
    u.add_argument("--confirm", action="store_true")

    d = sub.add_parser("delete", help="Delete a draft.")
    d.add_argument("draft_id")
    d.add_argument("--confirm", action="store_true")

    return p


def _read_body(args) -> str:
    if args.body_file:
        return Path(args.body_file).read_text()
    return args.body or ""


def _run_create(args) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
        assume_yes=getattr(args, "assume_yes", False),
    )
    if not args.confirm:
        print(f"(dry-run) would create draft subject={args.subject!r} to={args.to}",
              file=sys.stderr)
        return 0
    body = _read_body(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    op = Operation(
        op_id=new_op_id(), action="mail.draft.create",
        drive_id=derive_mailbox_upn(args.mailbox), item_id="",
        args={
            "subject": args.subject,
            "body": body,
            "body_type": args.body_type,
            "to": list(args.to),
            "cc": list(args.cc),
            "bcc": list(args.bcc),
            "importance": args.importance,
            "auth_mode": auth_mode,
        },
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_create_draft(op, graph, logger, before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    new_id = (result.after or {}).get("id", "")
    print(f"[{op.op_id}] ok — created draft {new_id}")
    return 0


def _run_update(args) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
        assume_yes=getattr(args, "assume_yes", False),
    )
    if not args.confirm:
        print(f"(dry-run) would update draft {args.draft_id}", file=sys.stderr)
        return 0

    call_args: dict = {"auth_mode": auth_mode}
    if args.subject is not None:
        call_args["subject"] = args.subject
    if args.body is not None or args.body_file is not None:
        call_args["body"] = _read_body(args)
        if args.body_type:
            call_args["body_type"] = args.body_type
    if args.to:
        call_args["to"] = list(args.to)
    if args.cc:
        call_args["cc"] = list(args.cc)
    if args.bcc:
        call_args["bcc"] = list(args.bcc)

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    from m365ctl.mail.messages import get_message
    try:
        msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                          message_id=args.draft_id)
        before = {
            "subject": msg.subject,
            "body": {"contentType": (msg.body.content_type if msg.body else "text"),
                     "content": (msg.body.content if msg.body else "")},
        }
    except Exception:
        before = {}

    op = Operation(
        op_id=new_op_id(), action="mail.draft.update",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.draft_id,
        args=call_args,
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_update_draft(op, graph, logger, before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — updated draft {args.draft_id}")
    return 0


def _run_delete(args) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
        assume_yes=getattr(args, "assume_yes", False),
    )
    if not args.confirm:
        print(f"(dry-run) would delete draft {args.draft_id}", file=sys.stderr)
        return 0

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    from m365ctl.mail.messages import get_message
    try:
        msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                          message_id=args.draft_id)
        before = {
            "subject": msg.subject,
            "body": {"contentType": (msg.body.content_type if msg.body else "text"),
                     "content": (msg.body.content if msg.body else "")},
            "toRecipients": [
                {"emailAddress": {"address": a.address, "name": a.name}}
                for a in msg.to
            ],
            "ccRecipients": [
                {"emailAddress": {"address": a.address, "name": a.name}}
                for a in msg.cc
            ],
            "bccRecipients": [
                {"emailAddress": {"address": a.address, "name": a.name}}
                for a in msg.bcc
            ],
        }
    except Exception:
        before = {}

    op = Operation(
        op_id=new_op_id(), action="mail.draft.delete",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.draft_id,
        args={"auth_mode": auth_mode},
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_delete_draft(op, graph, logger, before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — deleted draft {args.draft_id}")
    return 0


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.subcommand == "create":
        return _run_create(args)
    if args.subcommand == "update":
        return _run_update(args)
    if args.subcommand == "delete":
        return _run_delete(args)
    return 2
