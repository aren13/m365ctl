"""`od-auth` subcommands: login and whoami."""
from __future__ import annotations

import argparse
from pathlib import Path

from m365ctl.common.auth import (
    AppOnlyCredential,
    AuthError,
    DelegatedCredential,
)
from m365ctl.common.config import load_config
from m365ctl.common.graph import GraphClient


def run_login(config_path: Path) -> int:
    cfg = load_config(config_path)
    cred = DelegatedCredential(cfg)
    token = cred.login()
    print(f"Logged in. Token length: {len(token)}. Cache persisted.")
    return 0


def run_whoami(config_path: Path) -> int:
    cfg = load_config(config_path)

    print("m365ctl")
    print("======================")
    print(f"Tenant:                {cfg.tenant_id}")

    # --- Delegated flow --------------------------------------------------
    delegated = DelegatedCredential(cfg)
    try:
        token = delegated.get_token()
        graph = GraphClient(token_provider=lambda: token)
        me = graph.get("/me")
        display = me.get("displayName", "?")
        upn = me.get("userPrincipalName", "?")
        print(f"Delegated identity:    {display} <{upn}>")
    except AuthError as e:
        print(f"Delegated identity:    (not available - {e})")

    # --- App-only flow ---------------------------------------------------
    app_only = AppOnlyCredential(cfg)
    info = app_only.cert_info
    try:
        token = app_only.get_token()
        graph = GraphClient(token_provider=lambda: token)
        app = graph.get(f"/applications(appId='{cfg.client_id}')")
        app_name = app.get("displayName", "?")
        print(f"App-only identity:     {app_name} (appId {cfg.client_id})")
    except AuthError as e:
        print(f"App-only identity:     (not available - {e})")

    print(
        f"App-only cert:         {info.subject}, "
        f"thumbprint {info.thumbprint}, "
        f"expires {info.not_after_utc} ({info.days_until_expiry} days)"
    )

    if info.days_until_expiry < 60:
        print(
            f"  WARN: cert expires in {info.days_until_expiry} days - rotate soon."
        )

    print("Catalog:               not yet built (Plan 2)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-auth")
    p.add_argument(
        "--config",
        default="config.toml",
        help="Path to config.toml (default: config.toml in current dir)",
    )
    sub = p.add_subparsers(dest="subcommand", required=True)
    sub.add_parser("login", help="Device-code sign-in; caches token.")
    sub.add_parser("whoami", help="Print identity, scopes, cert expiry.")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config)
    if args.subcommand == "login":
        return run_login(config_path)
    if args.subcommand == "whoami":
        return run_whoami(config_path)
    return 2
