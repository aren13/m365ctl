"""`m365ctl mail list` — list messages in a folder with OData filters."""
from __future__ import annotations

import argparse
import sys

from m365ctl.common.graph import GraphClient
from m365ctl.mail.cli._common import (
    add_common_args,
    emit_json_lines,
    load_and_authorize,
)
from m365ctl.mail.folders import FolderNotFound, resolve_folder_path
from m365ctl.mail.messages import MessageListFilters, list_messages


def _print_human(messages) -> None:
    for m in messages:
        flag = "!" if m.flag.status == "flagged" else " "
        unread = "U" if not m.is_read else " "
        sender = m.from_addr.address or m.sender.address
        received = m.received_at.isoformat(timespec="minutes")
        print(f"{flag}{unread} {received}  {sender:<40}  {m.subject}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail list")
    add_common_args(p)
    p.add_argument("--folder", default="Inbox",
                   help="Folder path or well-known name (default: Inbox).")
    p.add_argument("--from", dest="from_address",
                   help="Filter by sender address (exact match).")
    p.add_argument("--subject", dest="subject_contains",
                   help="Filter by substring in subject.")
    p.add_argument("--since", help="ISO-8601 lower bound on receivedDateTime.")
    p.add_argument("--until", help="ISO-8601 upper bound on receivedDateTime.")
    p.add_argument("--unread", action="store_true", help="Only unread messages.")
    p.add_argument("--read", action="store_true", help="Only already-read messages.")
    p.add_argument("--has-attachments", action="store_true")
    p.add_argument("--importance", choices=("low", "normal", "high"))
    p.add_argument("--focus", choices=("focused", "other"))
    p.add_argument("--category", help="Filter by category name (exact match on one entry).")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--page-size", type=int, default=50)
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    # Resolve folder path → id.
    try:
        folder_id = resolve_folder_path(
            args.folder, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
    except FolderNotFound as e:
        print(f"mail list: {e}", file=sys.stderr)
        return 2

    # Unread/read resolution.
    if args.unread and args.read:
        print("mail list: --unread and --read are mutually exclusive", file=sys.stderr)
        return 2
    unread_flag: bool | None = None
    if args.unread:
        unread_flag = True
    elif args.read:
        unread_flag = False

    filters = MessageListFilters(
        unread=unread_flag,
        from_address=args.from_address,
        subject_contains=args.subject_contains,
        since=args.since,
        until=args.until,
        has_attachments=True if args.has_attachments else None,
        importance=args.importance,
        focus=args.focus,
        category=args.category,
    )

    msgs = list_messages(
        graph,
        mailbox_spec=args.mailbox,
        auth_mode=auth_mode,
        folder_id=folder_id,
        parent_folder_path=args.folder,
        filters=filters,
        limit=args.limit,
        page_size=args.page_size,
    )

    if args.json:
        emit_json_lines(msgs)
    else:
        _print_human(msgs)
    return 0
