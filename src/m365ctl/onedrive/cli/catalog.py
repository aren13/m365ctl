"""`od-catalog` subcommands: refresh and status."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.auth import AppOnlyCredential, DelegatedCredential
from m365ctl.onedrive.catalog.crawl import CrawlResult, crawl_drive, resolve_scope
from m365ctl.onedrive.catalog.db import open_catalog
from m365ctl.common.config import load_config
from m365ctl.common.graph import GraphClient
from m365ctl.common.prompts import TTYUnavailable, confirm_or_abort

_LARGE_SCOPE_THRESHOLD = 5


def _credential_for_scope(scope: str, cfg):
    """'me' -> delegated; everything else (drive:, site:, tenant) -> app-only."""
    if scope == "me":
        return DelegatedCredential(cfg)
    return AppOnlyCredential(cfg)


def run_refresh(*, config_path: Path, scope: str, assume_yes: bool = False) -> int:
    cfg = load_config(config_path)
    cred = _credential_for_scope(scope, cfg)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    drives = resolve_scope(scope, graph)
    print(f"Resolved {len(drives)} drive(s) under scope {scope!r}.")

    if len(drives) > _LARGE_SCOPE_THRESHOLD:
        print("Preview:")
        for d in drives[:20]:
            print(f"  - {d.drive_id}  {d.display_name}  ({d.owner})")
        if len(drives) > 20:
            print(f"  ... and {len(drives) - 20} more")
        try:
            proceed = confirm_or_abort(
                f"Proceed with refreshing {len(drives)} drive(s)?",
                assume_yes=assume_yes,
            )
        except TTYUnavailable:
            # No controlling terminal (CI, agent sandbox, detached session).
            # Fail safe: treat as abort rather than crashing. The operator
            # can pass --yes to bypass the gate intentionally.
            print(
                "No /dev/tty available to confirm. Re-run with --yes to "
                "proceed non-interactively. Aborted.",
                file=sys.stderr,
            )
            return 1
        if not proceed:
            print("Aborted by user.", file=sys.stderr)
            return 1

    results: list[CrawlResult] = []
    with open_catalog(cfg.catalog.path) as conn:
        for drive in drives:
            print(f"  - {drive.drive_id} ({drive.display_name}, {drive.owner})")
            result = crawl_drive(drive, graph, conn)
            results.append(result)
            print(f"    items seen: {result.items_seen}")

    print(f"Done. Catalog: {cfg.catalog.path}")
    return 0


def run_status(*, config_path: Path) -> int:
    cfg = load_config(config_path)
    with open_catalog(cfg.catalog.path) as conn:
        drives = conn.execute(
            "SELECT drive_id, display_name, owner, last_refreshed_at "
            "FROM drives ORDER BY drive_id"
        ).fetchall()
        (item_total,) = conn.execute("SELECT COUNT(*) FROM items").fetchone()
        (file_total,) = conn.execute(
            "SELECT COUNT(*) FROM items WHERE is_folder = false AND is_deleted = false"
        ).fetchone()
        (byte_total,) = conn.execute(
            "SELECT COALESCE(SUM(size), 0) FROM items "
            "WHERE is_folder = false AND is_deleted = false"
        ).fetchone()

    print(f"Catalog: {cfg.catalog.path}")
    print(f"Drives: {len(drives)}")
    for d in drives:
        print(f"  {d[0]}  {d[1]} ({d[2]})  last refreshed {d[3]}")
    print(f"Items:  {item_total} total ({file_total} live files)")
    print(f"Bytes:  {byte_total:,}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-catalog")
    p.add_argument("--config", default="config.toml")
    sub = p.add_subparsers(dest="subcommand", required=True)

    refresh = sub.add_parser("refresh", help="Delta-crawl a scope into the catalog.")
    refresh.add_argument(
        "--scope",
        required=True,
        help="'me', 'drive:<id>', 'site:<slug-or-id>', or 'tenant'",
    )
    refresh.add_argument(
        "--yes",
        dest="assume_yes",
        action="store_true",
        help="Skip the >5-drive preview/confirm prompt.",
    )
    sub.add_parser("status", help="Print catalog summary.")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config)
    if args.subcommand == "refresh":
        return run_refresh(
            config_path=config_path,
            scope=args.scope,
            assume_yes=args.assume_yes,
        )
    if args.subcommand == "status":
        return run_status(config_path=config_path)
    return 2
