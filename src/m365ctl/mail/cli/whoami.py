"""`m365ctl mail whoami` — identity, scopes, mailbox access, catalog stub."""
from __future__ import annotations

import argparse
from pathlib import Path

from m365ctl.common.auth import (
    AppOnlyCredential,
    AuthError,
    DelegatedCredential,
    GRAPH_SCOPES_DELEGATED,
)
from m365ctl.common.config import load_config
from m365ctl.common.graph import GraphClient


_ENTRA_CONSENT_URL_TEMPLATE = (
    "https://login.microsoftonline.com/{tenant}/adminconsent?client_id={client}"
)
_REQUIRED_MAIL_SCOPES = ("Mail.ReadWrite", "Mail.Send", "MailboxSettings.ReadWrite")


def run_whoami(config_path: Path) -> int:
    cfg = load_config(config_path)

    print("m365ctl mail")
    print("============")
    print(f"Tenant:                {cfg.tenant_id}")

    # Declared scopes.
    missing: list[str] = []
    for s in _REQUIRED_MAIL_SCOPES:
        if s not in GRAPH_SCOPES_DELEGATED:
            missing.append(s)
    print(f"Declared delegated scopes: {len(GRAPH_SCOPES_DELEGATED)} total")
    if missing:
        print(f"  MISSING in code: {', '.join(missing)}")
        return 2

    # Delegated probe: hit /me and /me/mailFolders/inbox.
    delegated = DelegatedCredential(cfg)
    try:
        token = delegated.get_token()
        graph = GraphClient(token_provider=lambda: token)
        me = graph.get("/me")
        print(f"Delegated identity:    {me.get('displayName', '?')} <{me.get('userPrincipalName', '?')}>")
        try:
            inbox = graph.get("/me/mailFolders/inbox")
            print(
                f"Mail access (me):      OK — /Inbox totals "
                f"{inbox.get('totalItemCount', 0)} items, "
                f"{inbox.get('unreadItemCount', 0)} unread"
            )
        except Exception as e:
            msg = str(e)
            first_line = msg.splitlines()[0] if msg else repr(e)
            print(f"Mail access (me):      FAILED — {first_line}")
            if "403" in msg or "AccessDenied" in msg or "consent" in msg.lower():
                consent_url = _ENTRA_CONSENT_URL_TEMPLATE.format(
                    tenant=cfg.tenant_id, client=cfg.client_id,
                )
                print(f"  Remediation: grant admin consent at:\n    {consent_url}")
    except AuthError as e:
        print(f"Delegated identity:    (not available - {e})")

    # App-only status.
    try:
        app_only = AppOnlyCredential(cfg)
        info = app_only.cert_info
        print(
            f"App-only cert:         {info.subject}, thumbprint {info.thumbprint}, "
            f"expires {info.not_after_utc} ({info.days_until_expiry} days)"
        )
    except Exception as e:
        print(f"App-only cert:         (not available - {e})")

    # Catalog stats (best effort — missing file is fine, just say "not built").
    try:
        from m365ctl.mail.catalog.db import open_catalog
        from m365ctl.mail.catalog.queries import summary
        cat_path = cfg.mail.catalog_path
        if cat_path.exists():
            with open_catalog(cat_path) as conn:
                stats = summary(conn, mailbox_upn="me")
            print(
                f"Mail catalog:          {cat_path} — "
                f"{stats['messages_total']} messages, "
                f"{stats['folders_total']} folders, "
                f"refreshed {stats['last_refreshed_at'] or '(never)'}"
            )
        else:
            print(f"Mail catalog:          {cat_path} (not built — run `mail catalog refresh`)")
    except Exception as e:
        print(f"Mail catalog:          (error reading: {e})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail whoami")
    p.add_argument("--config", default="config.toml")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_whoami(Path(args.config))
