"""Shared CLI helpers for `m365ctl mail <verb>` commands."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from m365ctl.common.auth import AppOnlyCredential, DelegatedCredential
from m365ctl.common.config import AuthMode, Config, load_config
from m365ctl.common.safety import assert_mailbox_allowed


def derive_mailbox_upn(spec: str) -> str:
    """Translate a mailbox spec to its UPN form for catalog keys.

    me                          -> me
    upn:alice@example.com       -> alice@example.com
    shared:team@example.com     -> team@example.com
    alice@example.com           -> alice@example.com  (passthrough)
    """
    if spec == "me":
        return "me"
    if spec.startswith("upn:") or spec.startswith("shared:"):
        return spec.split(":", 1)[1]
    return spec


def add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", default="config.toml", help="Path to config.toml (default: config.toml).")
    p.add_argument("--mailbox", default="me",
                   help="Mailbox: 'me' | 'upn:<addr>' | 'shared:<addr>' | '*' (default: me).")
    p.add_argument("--json", action="store_true", help="Emit NDJSON instead of human-readable output.")
    p.add_argument("--unsafe-scope", action="store_true",
                   help="Override allow_mailboxes via /dev/tty confirm (per mailbox).")
    p.add_argument(
        "--assume-yes",
        dest="assume_yes",
        action="store_true",
        help=(
            "Bypass non-irreversible TTY confirms (bulk thresholds, "
            "external-recipient gate, unsafe-scope). Requires "
            "[safety].allow_no_tty_confirm = true in config.toml. "
            "Does NOT bypass the literal-YES gates in mail clean / mail empty."
        ),
    )


def _validate_assume_yes(cfg: Config, args: argparse.Namespace) -> None:
    """Reject ``--yes`` unless ``[safety].allow_no_tty_confirm`` is set.

    The config gate is what prevents an agent from bypassing TTY confirms
    just by appending ``--yes`` to the command line — the operator must
    have opted in via config.toml first. On rejection, exits with code 2
    after printing a helpful message to stderr.
    """
    if getattr(args, "assume_yes", False) and not cfg.safety.allow_no_tty_confirm:
        print(
            "--yes requires [safety].allow_no_tty_confirm = true in "
            f"{args.config}. Set it explicitly in config.toml to opt "
            "advanced operators into bypassing TTY confirms.",
            file=sys.stderr,
        )
        sys.exit(2)


def load_and_authorize(
    args: argparse.Namespace,
) -> tuple[Config, AuthMode, DelegatedCredential | AppOnlyCredential]:
    """Load config, gate the requested mailbox, and return (cfg, auth_mode, credential)."""
    cfg = load_config(Path(args.config))
    _validate_assume_yes(cfg, args)
    mailbox_spec = args.mailbox
    auth_mode: AuthMode = cfg.default_auth if mailbox_spec == "me" else "app-only"
    assert_mailbox_allowed(
        mailbox_spec, cfg, auth_mode=auth_mode, unsafe_scope=args.unsafe_scope,
        assume_yes=getattr(args, "assume_yes", False),
    )
    cred: DelegatedCredential | AppOnlyCredential = (
        DelegatedCredential(cfg) if auth_mode == "delegated" else AppOnlyCredential(cfg)
    )
    return cfg, auth_mode, cred


def _json_default(o: Any) -> Any:
    if is_dataclass(o) and not isinstance(o, type):
        return asdict(o)
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, bytes):
        import base64
        return base64.b64encode(o).decode("ascii")
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"not JSON-serializable: {type(o).__name__}")


def emit_json_lines(records: Iterable[Any]) -> None:
    for rec in records:
        sys.stdout.write(json.dumps(rec, default=_json_default, ensure_ascii=False))
        sys.stdout.write("\n")
