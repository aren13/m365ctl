"""`m365ctl mail attach list|get` — read-only attachments."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.graph import GraphClient
from m365ctl.mail.attachments import get_attachment_content, list_attachments
from m365ctl.mail.cli._common import add_common_args, emit_json_lines, load_and_authorize


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail attach")
    add_common_args(p)
    sub = p.add_subparsers(dest="subcommand", required=True)
    lst = sub.add_parser("list")
    lst.add_argument("message_id")
    get_p = sub.add_parser("get")
    get_p.add_argument("message_id")
    get_p.add_argument("attachment_id")
    get_p.add_argument("--out", help="Path to write the attachment to. Default: stdout.")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    if args.subcommand == "list":
        out = list_attachments(
            graph,
            mailbox_spec=args.mailbox,
            auth_mode=auth_mode,
            message_id=args.message_id,
        )
        if args.json:
            emit_json_lines(out)
        else:
            for a in out:
                print(
                    f"{a.kind:<9}  {a.size:>10}  {a.content_type:<40}  "
                    f"{a.name}  (id: {a.id})"
                )
        return 0

    if args.subcommand == "get":
        data = get_attachment_content(
            graph,
            mailbox_spec=args.mailbox,
            auth_mode=auth_mode,
            message_id=args.message_id,
            attachment_id=args.attachment_id,
        )
        if args.out:
            Path(args.out).write_bytes(data)
            print(f"Wrote {len(data)} bytes to {args.out}", file=sys.stderr)
        else:
            sys.stdout.buffer.write(data)
        return 0

    return 2
