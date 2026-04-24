"""`m365ctl mail folders` — list / tree view of mail folders."""
from __future__ import annotations

import argparse

from m365ctl.common.graph import GraphClient
from m365ctl.common.safety import is_folder_denied
from m365ctl.mail.cli._common import (
    add_common_args,
    emit_json_lines,
    load_and_authorize,
)
from m365ctl.mail.folders import list_folders


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail folders")
    add_common_args(p)
    p.add_argument("--tree", action="store_true", help="Tree view (indent by depth).")
    p.add_argument("--with-counts", action="store_true", help="Show total/unread counts.")
    p.add_argument("--include-hidden", action="store_true", help="Include hidden folders.")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    folders = list(list_folders(
        graph,
        mailbox_spec=args.mailbox,
        auth_mode=auth_mode,
        include_hidden=args.include_hidden,
    ))
    folders = [f for f in folders if not is_folder_denied(f.path, cfg)]

    if args.json:
        emit_json_lines(folders)
        return 0

    if args.tree:
        for f in folders:
            depth = f.path.count("/")
            indent = "  " * depth
            counts = f"  ({f.total_items}/{f.unread_items})" if args.with_counts else ""
            print(f"{indent}{f.display_name}{counts}")
    else:
        for f in folders:
            counts = f"  ({f.total_items}/{f.unread_items})" if args.with_counts else ""
            print(f"{f.path}{counts}")
    return 0
