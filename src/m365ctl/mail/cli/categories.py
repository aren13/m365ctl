"""`m365ctl mail categories list` — list master categories (read-only)."""
from __future__ import annotations

import argparse

from m365ctl.common.graph import GraphClient
from m365ctl.mail.categories import list_master_categories
from m365ctl.mail.cli._common import add_common_args, emit_json_lines, load_and_authorize


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail categories")
    add_common_args(p)
    sub = p.add_subparsers(dest="subcommand", required=False)
    sub.add_parser("list", help="List master categories (default).")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    cats = list_master_categories(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)

    if args.json:
        emit_json_lines(cats)
    else:
        for c in cats:
            print(f"{c.color:<12}  {c.display_name}  (id: {c.id})")
    return 0
