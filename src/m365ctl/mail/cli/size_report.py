"""`m365ctl mail size-report` — catalog-driven folder size breakdown."""
from __future__ import annotations

import argparse
import sys

from m365ctl.mail.cli._common import add_common_args, emit_json_lines, load_and_authorize
from m365ctl.mail.convenience.size_report import build_size_report
from m365ctl.mail.mutate._common import derive_mailbox_upn


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="m365ctl mail size-report",
        description=(
            "Per-folder message-count + total-size breakdown from the mail "
            "catalog. Sorted by total_size desc."
        ),
    )
    add_common_args(p)
    p.add_argument(
        "--top", type=int, default=None,
        help="Show only the top N folders by total_size (default: unlimited).",
    )
    return p


def _format_size(n: int) -> str:
    """Pretty-print a byte count (powers of 1024)."""
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    f = float(n)
    for unit in units:
        if f < 1024 or unit == units[-1]:
            return f"{f:7.1f} {unit}"
        f /= 1024
    return f"{n} B"


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    cfg, _auth_mode, _cred = load_and_authorize(args)
    mailbox_upn = derive_mailbox_upn(args.mailbox)

    rows = build_size_report(
        cfg.mail.catalog_path,
        mailbox_upn=mailbox_upn,
        top=args.top,
    )

    if args.json:
        emit_json_lines(rows)
        return 0

    if not rows:
        print("(no folders in catalog — run 'mail catalog refresh' first)",
              file=sys.stderr)
        return 0

    print(f"{'count':>8}  {'size':>12}  folder")
    for r in rows:
        path = r.get("parent_folder_path") or "(unknown)"
        count = int(r.get("message_count") or 0)
        size = int(r.get("total_size") or 0)
        print(f"{count:>8}  {_format_size(size):>12}  {path}")
    return 0


__all__ = ["main", "build_parser"]
