"""`m365ctl mail search <query>` — Graph search and/or catalog LIKE."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.config import load_config
from m365ctl.common.graph import GraphClient
from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.cli._common import (
    add_common_args,
    emit_json_lines,
    load_and_authorize,
)
from m365ctl.mail.messages import search_messages_graph

_LOCAL_COLUMNS = """
SELECT mailbox_upn, message_id, internet_message_id, parent_folder_path,
       subject, from_address, from_name, to_addresses, received_at, body_preview
FROM mail_messages
WHERE mailbox_upn = ?
  AND is_deleted = false
  AND (
       LOWER(COALESCE(subject, ''))      LIKE ? OR
       LOWER(COALESCE(from_address, '')) LIKE ? OR
       LOWER(COALESCE(from_name, ''))    LIKE ? OR
       LOWER(COALESCE(to_addresses, '')) LIKE ? OR
       LOWER(COALESCE(body_preview, '')) LIKE ?
  )
ORDER BY received_at DESC
LIMIT ?
"""


def _derive_mailbox_upn(mailbox_spec: str) -> str:
    if mailbox_spec == "me":
        return "me"
    if mailbox_spec.startswith("upn:") or mailbox_spec.startswith("shared:"):
        return mailbox_spec.split(":", 1)[1]
    return mailbox_spec


def _query_local(*, catalog_path: Path, mailbox_upn: str, query: str, limit: int):
    if not catalog_path.exists():
        return None  # signal "empty catalog"
    needle = f"%{query.lower()}%"
    with open_catalog(catalog_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM mail_messages WHERE mailbox_upn = ?",
            [mailbox_upn],
        ).fetchone()
        assert row is not None
        (count,) = row
        if count == 0:
            return None
        cur = conn.execute(
            _LOCAL_COLUMNS,
            [mailbox_upn, needle, needle, needle, needle, needle, limit],
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail search")
    add_common_args(p)
    p.add_argument("query", help='Search expression (e.g. "subject:meeting").')
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--local", action="store_true",
                   help="Only the local DuckDB catalog (no Graph call).")
    return p


def _print_human(rows: list[dict]) -> None:
    if not rows:
        print("(no local hits)")
        return
    for r in rows:
        received = r["received_at"]
        rec_str = received.isoformat(timespec="minutes") if received else ""
        sender = r.get("from_address") or ""
        print(f"{rec_str}  {sender:<40}  {r.get('subject', '')}")


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.local:
        cfg = load_config(Path(args.config))
        mailbox_upn = _derive_mailbox_upn(args.mailbox)
        rows = _query_local(
            catalog_path=cfg.mail.catalog_path,
            mailbox_upn=mailbox_upn,
            query=args.query,
            limit=args.limit,
        )
        if rows is None:
            print(
                "mail search: catalog empty — run `mail catalog refresh` first.",
                file=sys.stderr,
            )
            return 0
        if args.json:
            emit_json_lines(rows)
        else:
            _print_human(rows)
        return 0

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
