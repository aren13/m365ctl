"""`m365ctl mail digest` — unread mail digest (print or self-mail).

Composes catalog reads + the existing send-inline executor. No new audit
namespaces or Graph endpoints — `--send-to` goes through `mail.send` /new.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.cli._common import add_common_args, emit_json_lines, load_and_authorize
from m365ctl.mail.convenience.digest import (
    DigestError,
    build_digest,
    parse_since,
    render_html,
    render_text,
)
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.send import execute_send_new


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="m365ctl mail digest",
        description=(
            "Print (or send) a digest of unread messages. "
            "Default: query catalog, render text, print to stdout."
        ),
    )
    add_common_args(p)
    p.add_argument(
        "--since", default="24h",
        help="Window: '24h' / '3d' / ISO timestamp (default: 24h).",
    )
    p.add_argument(
        "--limit", type=int, default=20,
        help="Max recent messages to include (default: 20).",
    )
    p.add_argument(
        "--send-to",
        help="Address (or 'me') to mail the HTML digest to. Requires --confirm.",
    )
    p.add_argument(
        "--confirm", action="store_true",
        help="Required when --send-to is given (otherwise dry-run).",
    )
    return p


def _load_unread_rows(catalog_path: Path, mailbox_upn: str) -> list[dict]:
    if not catalog_path.exists():
        return []
    with open_catalog(catalog_path) as conn:
        cur = conn.execute(
            """
            SELECT message_id, subject, from_address, received_at, categories
            FROM mail_messages
            WHERE mailbox_upn = ?
              AND COALESCE(is_read, false) = false
              AND COALESCE(is_deleted, false) = false
            """,
            [mailbox_upn],
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _resolve_send_to(graph: GraphClient, addr: str) -> str:
    if addr == "me":
        me = graph.get("/me")
        upn = me.get("userPrincipalName") or me.get("mail") or ""
        if not upn:
            raise RuntimeError("could not resolve /me userPrincipalName")
        return str(upn)
    return addr


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.send_to and not args.confirm:
        # Dry-run notice on stderr; no work performed.
        print(
            f"(dry-run) would send digest to {args.send_to!r}. "
            f"Re-run with --confirm to actually send.",
            file=sys.stderr,
        )
        return 0

    cfg, auth_mode, cred = load_and_authorize(args)
    mailbox_upn = derive_mailbox_upn(args.mailbox)

    now = datetime.now(timezone.utc)
    try:
        since = parse_since(args.since, now=now)
    except DigestError as e:
        print(f"mail digest: {e}", file=sys.stderr)
        return 2

    rows = _load_unread_rows(cfg.mail.catalog_path, mailbox_upn)
    d = build_digest(rows, since=since, now=now, limit=args.limit)

    if args.send_to:
        # Already gated above; here we have --confirm.
        assert_mail_target_allowed(
            cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
            unsafe_scope=args.unsafe_scope,
            assume_yes=getattr(args, "assume_yes", False),
        )
        token = cred.get_token()
        graph = GraphClient(token_provider=lambda: token)
        target = _resolve_send_to(graph, args.send_to)
        body = render_html(d)
        subject = f"[Digest] {d.total} unread since {args.since}"
        op = Operation(
            op_id=new_op_id(),
            action="mail.send",
            drive_id=mailbox_upn,
            item_id="",
            args={
                "subject": subject,
                "body": body,
                "body_type": "html",
                "to": [target],
                "cc": [],
                "bcc": [],
                "importance": None,
                "new": True,
                "auth_mode": auth_mode,
            },
        )
        logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
        result = execute_send_new(op, graph, logger, before={})
        if result.status != "ok":
            print(f"error: {result.error}", file=sys.stderr)
            return 1
        print(f"[{op.op_id}] ok — digest sent to {target}")
        return 0

    if args.json:
        emit_json_lines(d.recent)
        return 0

    sys.stdout.write(render_text(d))
    return 0


__all__ = ["main", "build_parser"]
