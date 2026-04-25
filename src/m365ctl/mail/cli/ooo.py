"""`m365ctl mail ooo {show, on, off}` — out-of-office / auto-reply.

Phase 9 G3.2: standalone dispatcher for the AutomaticRepliesSetting
mailbox setting. ``mail settings ooo`` (printer-only) remains for
backward compat.

Subcommands:
  show              Print current AutomaticRepliesSetting.
  on  --message     Enable (alwaysEnabled or scheduled if --start/--end given).
  off               Disable.

The 60-day safety gate fires inside ``execute_set_auto_reply``; here
we catch ``OOOTooLong`` and surface a clear message instructing the
operator to re-run with ``--force``.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.cli._common import add_common_args, emit_json_lines, load_and_authorize
from m365ctl.mail.mutate._common import derive_mailbox_upn
from m365ctl.mail.mutate.settings import OOOTooLong, execute_set_auto_reply
from m365ctl.mail.settings import get_auto_reply


_AUDIENCES = ("none", "contactsOnly", "all")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail ooo")
    add_common_args(p)
    sub = p.add_subparsers(dest="subcommand", required=True)

    show = sub.add_parser("show", help="Print current automatic-replies setting.")
    add_common_args(show)

    off = sub.add_parser("off", help="Disable automatic replies.")
    add_common_args(off)
    off.add_argument("--confirm", action="store_true")

    on = sub.add_parser("on", help="Enable automatic replies.")
    add_common_args(on)
    on.add_argument("--message", required=True, help="Internal reply message.")
    on.add_argument("--audience", choices=_AUDIENCES, default="all")
    on.add_argument("--start", help="ISO start (UTC). With --end => scheduled.")
    on.add_argument("--end", help="ISO end (UTC). With --start => scheduled.")
    on.add_argument("--external-message", dest="external_message",
                    help="External reply message (defaults to --message).")
    on.add_argument("--force", action="store_true",
                    help="Bypass the 60-day OOO safety gate.")
    on.add_argument("--confirm", action="store_true")
    return p


# ---- handlers --------------------------------------------------------------

def _run_show(args: argparse.Namespace) -> int:
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    ar = get_auto_reply(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)
    if args.json:
        emit_json_lines([ar])
    else:
        print(f"status:             {ar.status}")
        print(f"external_audience:  {ar.external_audience}")
        print(f"scheduled_start:    {ar.scheduled_start}")
        print(f"scheduled_end:      {ar.scheduled_end}")
        print(f"internal_reply:     {ar.internal_reply_message!r}")
        print(f"external_reply:     {ar.external_reply_message!r}")
    return 0


def _dispatch(args: argparse.Namespace, body: dict[str, Any], *, force: bool) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    op = Operation(
        op_id=new_op_id(),
        action="mail.settings.auto-reply",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id="",
        args={
            "mailbox_spec": args.mailbox,
            "auth_mode": auth_mode,
            "auto_reply": body,
            "force": force,
        },
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    try:
        result = execute_set_auto_reply(op, graph, logger, before={})
    except OOOTooLong as e:
        # Extract the day count out of the exception text.
        days = "?"
        for tok in str(e).replace(",", " ").split():
            if tok.isdigit():
                days = tok
                break
        print(
            f"OOO duration {days} days exceeds 60-day safety gate. "
            f"Re-run with --force to confirm.",
            file=sys.stderr,
        )
        return 1
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — auto-reply updated ({body.get('status')})")
    return 0


def _run_off(args: argparse.Namespace) -> int:
    if not args.confirm:
        print(
            "(dry-run) would disable automatic replies; "
            "re-run with --confirm to apply.",
            file=sys.stderr,
        )
        return 0
    body: dict[str, Any] = {"status": "disabled"}
    return _dispatch(args, body, force=False)


def _run_on(args: argparse.Namespace) -> int:
    has_start = bool(args.start)
    has_end = bool(args.end)
    if has_start ^ has_end:
        print(
            "mail ooo on: --start and --end must be provided together (or neither).",
            file=sys.stderr,
        )
        return 2

    external = args.external_message if args.external_message else args.message
    body: dict[str, Any] = {
        "externalAudience": args.audience,
        "internalReplyMessage": args.message,
        "externalReplyMessage": external,
    }
    if has_start and has_end:
        body["status"] = "scheduled"
        body["scheduledStartDateTime"] = {"dateTime": args.start, "timeZone": "UTC"}
        body["scheduledEndDateTime"] = {"dateTime": args.end, "timeZone": "UTC"}
    else:
        body["status"] = "alwaysEnabled"

    if not args.confirm:
        print(
            f"(dry-run) would enable OOO ({body['status']}); "
            f"re-run with --confirm to apply.",
            file=sys.stderr,
        )
        return 0
    return _dispatch(args, body, force=args.force)


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.subcommand == "show":
        return _run_show(args)
    if args.subcommand == "off":
        return _run_off(args)
    if args.subcommand == "on":
        return _run_on(args)
    return 2
