"""`m365ctl mail settings show|ooo` — read-only mailbox settings."""
from __future__ import annotations

import argparse

from m365ctl.common.graph import GraphClient
from m365ctl.mail.cli._common import add_common_args, emit_json_lines, load_and_authorize
from m365ctl.mail.settings import get_auto_reply, get_settings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail settings")
    add_common_args(p)
    sub = p.add_subparsers(dest="subcommand", required=True)
    sub.add_parser("show", help="Print all mailbox settings.")
    sub.add_parser("ooo", help="Print the automatic-replies setting.")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    if args.subcommand == "show":
        s = get_settings(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)
        if args.json:
            emit_json_lines([s])
        else:
            print(f"timezone:       {s.timezone}")
            print(f"language:       {s.language.locale} ({s.language.display_name})")
            wh = s.working_hours
            days_str = ",".join(wh.days)
            print(f"working_hours:  {days_str} {wh.start_time}-{wh.end_time} {wh.time_zone}")
            print(f"auto_reply:     {s.auto_reply.status} (audience: {s.auto_reply.external_audience})")
            print(f"delegate_msgs:  {s.delegate_meeting_message_delivery}")
            print(f"date_format:    {s.date_format}")
            print(f"time_format:    {s.time_format}")
        return 0

    if args.subcommand == "ooo":
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
    return 2
