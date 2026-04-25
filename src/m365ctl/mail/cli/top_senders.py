"""`m365ctl mail top-senders` — top senders by message count.

Reuses ``parse_since`` from the digest convenience module to support the
``<N>h`` / ``<N>d`` / ISO duration grammar already documented for digest.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from m365ctl.mail.cli._common import add_common_args, emit_json_lines, load_and_authorize
from m365ctl.mail.convenience.digest import DigestError, parse_since
from m365ctl.mail.convenience.top_senders import build_top_senders
from m365ctl.mail.mutate._common import derive_mailbox_upn


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="m365ctl mail top-senders",
        description=(
            "Top senders by message count from the mail catalog. Optional "
            "--since filter restricts to messages received in the window."
        ),
    )
    add_common_args(p)
    p.add_argument(
        "--since", default=None,
        help="Window: '24h' / '7d' / ISO timestamp (default: all time).",
    )
    p.add_argument(
        "--limit", type=int, default=20,
        help="Max senders to return (default: 20).",
    )
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    cfg, _auth_mode, _cred = load_and_authorize(args)
    mailbox_upn = derive_mailbox_upn(args.mailbox)

    since: datetime | None = None
    if args.since is not None:
        try:
            since = parse_since(args.since, now=datetime.now(timezone.utc))
        except DigestError as e:
            print(f"mail top-senders: {e}", file=sys.stderr)
            return 2

    rows = build_top_senders(
        cfg.mail.catalog_path,
        mailbox_upn=mailbox_upn,
        since=since,
        limit=args.limit,
    )

    if args.json:
        emit_json_lines(rows)
        return 0

    if not rows:
        print("(no senders in catalog — run 'mail catalog refresh' first)",
              file=sys.stderr)
        return 0

    print(f"{'count':>8}  sender")
    for r in rows:
        addr = r.get("from_address") or "(unknown)"
        count = int(r.get("count") or 0)
        print(f"{count:>8}  {addr}")
    return 0


__all__ = ["main", "build_parser"]
