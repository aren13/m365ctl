"""`m365ctl mail get <message-id>` — fetch one message."""
from __future__ import annotations

import argparse
import sys

from m365ctl.common.graph import GraphClient
from m365ctl.mail.cli._common import (
    add_common_args,
    emit_json_lines,
    load_and_authorize,
)
from m365ctl.mail.messages import get_message


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail get")
    add_common_args(p)
    p.add_argument("message_id", help="Graph message id (from mail-list).")
    p.add_argument("--with-body", action="store_true", help="Include message body.")
    p.add_argument("--with-headers", action="store_true", help="Include raw Internet headers.")
    p.add_argument("--with-attachments", action="store_true",
                   help="Expand attachments list via $expand.")
    p.add_argument("--eml", action="store_true",
                   help="Emit as .eml (raw mime) instead of JSON/text. Deferred to Phase 11.")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.eml:
        print("mail get --eml: deferred to Phase 11 (export).", file=sys.stderr)
        return 2

    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    msg = get_message(
        graph,
        mailbox_spec=args.mailbox,
        auth_mode=auth_mode,
        message_id=args.message_id,
        with_attachments=args.with_attachments,
    )

    if args.json:
        emit_json_lines([msg])
    else:
        print(f"id:          {msg.id}")
        print(f"subject:     {msg.subject}")
        print(f"from:        {msg.from_addr.address}")
        print(f"to:          {', '.join(a.address for a in msg.to)}")
        if msg.cc:
            print(f"cc:          {', '.join(a.address for a in msg.cc)}")
        print(f"received:    {msg.received_at.isoformat()}")
        print(f"folder:      {msg.parent_folder_path}")
        print(f"flag:        {msg.flag.status}")
        print(f"read:        {msg.is_read}")
        if args.with_body and msg.body:
            print(f"body-type:   {msg.body.content_type}")
            print("body:")
            print(msg.body.content)
    return 0
