"""`m365ctl mail unsubscribe <message-id>` — RFC 2369 / RFC 8058 dispatcher.

Default (no ``--method``): print discovered methods, exit 0.

``--method http``  — hit the URL via ``httpx``. With one-click flag (RFC 8058)
                     POSTs ``List-Unsubscribe=One-Click``; otherwise GETs.
``--method mailto`` — print the address + suggested subject (don't auto-send).
``--method first``  — prefer ``http`` if available, fall back to ``mailto``.

Mutating actions (``http``) require ``--confirm`` unless ``--dry-run`` is set.
"""
from __future__ import annotations

import argparse
import sys

import httpx

from m365ctl.common.graph import GraphClient
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.convenience.unsubscribe import (
    UnsubscribeMethod,
    discover_methods,
)
from m365ctl.mail.endpoints import user_base


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="m365ctl mail unsubscribe",
        description=(
            "Discover and (optionally) act on List-Unsubscribe methods for a "
            "message. Default prints discovered methods only."
        ),
    )
    add_common_args(p)
    p.add_argument("message_id", help="Graph message id (from mail-list).")
    p.add_argument(
        "--method", choices=("http", "mailto", "first"), default=None,
        help="Action to take. Default: print discovered methods only.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Describe what would happen without performing the action.",
    )
    p.add_argument(
        "--confirm", action="store_true",
        help="Required for non-dry-run --method http.",
    )
    return p


def _print_methods(methods: list[UnsubscribeMethod]) -> None:
    for m in methods:
        flag = " [one-click]" if m.one_click else ""
        print(f"  {m.kind:>6}  {m.target}{flag}")


def _act_http(m: UnsubscribeMethod, *, dry_run: bool) -> int:
    verb = "POST" if m.one_click else "GET"
    if dry_run:
        print(f"would {verb} {m.target}")
        return 0
    if m.one_click:
        resp = httpx.post(
            m.target,
            data={"List-Unsubscribe": "One-Click"},
            timeout=10.0,
        )
    else:
        resp = httpx.get(m.target, timeout=10.0, follow_redirects=True)
    print(f"{verb} {m.target} → {resp.status_code}")
    if 200 <= resp.status_code < 400:
        return 0
    return 1


def _act_mailto(m: UnsubscribeMethod) -> int:
    # Strip mailto: prefix for display; the user's mail client handles the rest.
    addr = m.target[len("mailto:"):] if m.target.startswith("mailto:") else m.target
    print(
        f"To unsubscribe via mail, send a message to: {addr}\n"
        f"  Suggested subject: unsubscribe"
    )
    return 0


def _select_method(
    methods: list[UnsubscribeMethod], choice: str,
) -> UnsubscribeMethod | None:
    if choice == "http":
        return next((m for m in methods if m.kind == "https"), None)
    if choice == "mailto":
        return next((m for m in methods if m.kind == "mailto"), None)
    if choice == "first":
        for m in methods:
            if m.kind == "https":
                return m
        return next((m for m in methods if m.kind == "mailto"), None)
    return None


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    ub = user_base(args.mailbox, auth_mode=auth_mode)
    msg = graph.get(
        f"{ub}/messages/{args.message_id}",
        params={"$select": "internetMessageHeaders"},
    )
    methods = discover_methods(msg)

    if not methods:
        print("(no unsubscribe header)", file=sys.stderr)
        return 0

    if args.method is None:
        print(f"discovered {len(methods)} unsubscribe method(s):")
        _print_methods(methods)
        return 0

    chosen = _select_method(methods, args.method)
    if chosen is None:
        print(f"no '{args.method}' method available; discovered:",
              file=sys.stderr)
        _print_methods(methods)
        return 1

    if chosen.kind == "https":
        if not args.dry_run and not args.confirm:
            print(
                f"(dry-run) would hit {chosen.target}. "
                f"Re-run with --confirm to actually unsubscribe.",
                file=sys.stderr,
            )
            return 0
        return _act_http(chosen, dry_run=args.dry_run)
    return _act_mailto(chosen)


__all__ = ["main", "build_parser"]
