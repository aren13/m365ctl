"""`m365ctl mail catalog {refresh,status}` — DuckDB mirror of the mailbox."""
from __future__ import annotations

import argparse
from pathlib import Path

from m365ctl.common.auth import AppOnlyCredential, DelegatedCredential
from m365ctl.common.config import load_config
from m365ctl.common.graph import GraphClient
from m365ctl.common.safety import assert_mailbox_allowed
from m365ctl.mail.catalog.crawl import refresh_mailbox
from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.catalog.queries import summary
from m365ctl.mail.folders import resolve_folder_path


def _derive_mailbox_upn(mailbox_spec: str) -> str:
    if mailbox_spec == "me":
        return "me"
    if mailbox_spec.startswith("upn:") or mailbox_spec.startswith("shared:"):
        return mailbox_spec.split(":", 1)[1]
    return mailbox_spec


def _credential(cfg, *, auth_mode: str):
    if auth_mode == "delegated":
        return DelegatedCredential(cfg)
    return AppOnlyCredential(cfg)


def _run_refresh(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    mailbox_spec = args.mailbox
    auth_mode = cfg.default_auth if mailbox_spec == "me" else "app-only"
    assert_mailbox_allowed(
        mailbox_spec, cfg, auth_mode=auth_mode, unsafe_scope=args.unsafe_scope,
    )
    cred = _credential(cfg, auth_mode=auth_mode)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    folder_filter: str | None = None
    if args.folder:
        folder_filter = resolve_folder_path(
            args.folder, graph,
            mailbox_spec=mailbox_spec, auth_mode=auth_mode,
        )

    mailbox_upn = _derive_mailbox_upn(mailbox_spec)
    print(f"Mail catalog: {cfg.mail.catalog_path}")
    print(f"Mailbox:      {mailbox_upn}")
    if folder_filter:
        print(f"Folder:       {args.folder} ({folder_filter})")
    print("Refreshing...")

    with open_catalog(cfg.mail.catalog_path) as conn:
        outcomes = refresh_mailbox(
            graph,
            conn=conn,
            mailbox_spec=mailbox_spec,
            mailbox_upn=mailbox_upn,
            auth_mode=auth_mode,
            folder_filter=folder_filter,
            max_rounds=args.max_rounds,
        )
    for o in outcomes:
        marker = ""
        if o.status == "restarted":
            marker += " [restarted]"
        if o.truncated:
            marker += " [truncated — re-run to continue]"
        print(f"  {o.folder_path:<24} {o.messages_seen:>6} messages{marker}")
    print(f"Done. {len(outcomes)} folder(s) refreshed.")
    return 0


def _run_status(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    mailbox_upn = _derive_mailbox_upn(args.mailbox)
    print(f"Mail catalog: {cfg.mail.catalog_path}")
    print(f"Mailbox:      {mailbox_upn}")
    with open_catalog(cfg.mail.catalog_path) as conn:
        s = summary(conn, mailbox_upn=mailbox_upn)
        per_folder = conn.execute(
            "SELECT path, total_items, unread_items "
            "FROM mail_folders WHERE mailbox_upn = ? "
            "ORDER BY path",
            [mailbox_upn],
        ).fetchall()
    print(f"Folders:      {s['folders_total']}")
    print(f"Messages:     {s['messages_total']} live, {s['messages_deleted']} tombstoned")
    print(f"Last refresh: {s['last_refreshed_at'] or '(never)'}")
    if per_folder:
        print("Per-folder (server-reported counts):")
        for path, total, unread in per_folder:
            print(f"  {(path or ''):<32} {total or 0:>6} total  {unread or 0:>4} unread")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail catalog")
    sub = p.add_subparsers(dest="subcommand", required=True)

    refresh = sub.add_parser("refresh", help="Delta-sync the mailbox into the catalog.")
    refresh.add_argument("--config", default="config.toml")
    refresh.add_argument("--mailbox", default="me",
                         help="'me' | 'upn:<addr>' | 'shared:<addr>' (default: me)")
    refresh.add_argument("--folder",
                         help="Restrict refresh to one folder (path or well-known name).")
    refresh.add_argument(
        "--max-rounds", type=int, default=None, metavar="N",
        help=("Stop after N delta rounds per folder; the deltaLink is saved "
              "so a subsequent refresh resumes."),
    )
    refresh.add_argument("--unsafe-scope", action="store_true")

    status = sub.add_parser("status", help="Print catalog summary.")
    status.add_argument("--config", default="config.toml")
    status.add_argument("--mailbox", default="me")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.subcommand == "refresh":
        return _run_refresh(args)
    if args.subcommand == "status":
        return _run_status(args)
    return 2
