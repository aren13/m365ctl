"""`m365ctl mail search <query>` — server-side /search/query over messages."""
from __future__ import annotations

import argparse
import sys

from m365ctl.common.graph import GraphClient
from m365ctl.mail.cli._common import (
    add_common_args,
    emit_json_lines,
    load_and_authorize,
)
from m365ctl.mail.messages import search_messages_graph


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail search")
    add_common_args(p)
    p.add_argument("query", help='Search expression (KQL: from:alice AND subject:meeting).')
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--local", action="store_true",
                   help="Query the local DuckDB catalog (Phase 7).")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.local:
        print("mail search --local: catalog arrives in Phase 7.", file=sys.stderr)
        return 2

    _cfg, _auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    hits = list(search_messages_graph(graph, query=args.query, limit=args.limit))

    if args.json:
        emit_json_lines(hits)
    else:
        for m in hits:
            sender = m.from_addr.address or m.sender.address
            received = m.received_at.isoformat(timespec="minutes")
            print(f"{received}  {sender:<40}  {m.subject}")
    return 0
