"""`m365ctl mail delegate {list, grant, revoke}` — mailbox delegation.

Phase 12 G3: thin argparse front-end over `mail.mutate.delegate`. The
underlying executor shells out to `scripts/ps/Set-MailboxDelegate.ps1`,
which connects to Exchange Online via PowerShell — so this CLI does NOT
need a Graph token. We still load `config.toml` to locate the audit-log
ops_dir for `AuditLogger`.

Subcommands:
  list <mailbox-upn> [--json]
  grant <mailbox-upn> --to <delegate-upn> [--rights {FullAccess,SendAs,SendOnBehalf}] --confirm
  revoke <mailbox-upn> --to <delegate-upn> [--rights {FullAccess,SendAs,SendOnBehalf}] --confirm

The mailbox argument is a bare UPN (no `upn:` / `shared:` prefix) since
this verb manages mailbox-level permissions, not per-message routing.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.config import load_config
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.cli._common import emit_json_lines
from m365ctl.mail.mutate.delegate import (
    execute_grant,
    execute_revoke,
    list_delegates,
)

_RIGHTS_CHOICES = ("FullAccess", "SendAs", "SendOnBehalf")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail delegate")
    p.add_argument(
        "--config",
        default="config.toml",
        help="Path to config.toml (default: config.toml).",
    )
    sub = p.add_subparsers(dest="subcommand", required=True)

    p_list = sub.add_parser("list", help="List delegates on a mailbox.")
    p_list.add_argument("mailbox", help="Target mailbox UPN.")
    p_list.add_argument(
        "--json", action="store_true", help="Emit NDJSON instead of a table."
    )

    p_grant = sub.add_parser("grant", help="Grant a delegate permission.")
    p_grant.add_argument("mailbox", help="Target mailbox UPN.")
    p_grant.add_argument(
        "--to", dest="delegate", required=True, help="Delegate UPN."
    )
    p_grant.add_argument(
        "--rights", choices=_RIGHTS_CHOICES, default="FullAccess",
        help="Permission level (default: FullAccess).",
    )
    p_grant.add_argument(
        "--confirm", action="store_true",
        help="Required to actually apply the grant.",
    )

    p_revoke = sub.add_parser("revoke", help="Revoke a delegate permission.")
    p_revoke.add_argument("mailbox", help="Target mailbox UPN.")
    p_revoke.add_argument(
        "--to", dest="delegate", required=True, help="Delegate UPN."
    )
    p_revoke.add_argument(
        "--rights", choices=_RIGHTS_CHOICES, default="FullAccess",
        help="Permission level (default: FullAccess).",
    )
    p_revoke.add_argument(
        "--confirm", action="store_true",
        help="Required to actually apply the revoke.",
    )

    return p


# ---- handlers --------------------------------------------------------------


def _run_list(args: argparse.Namespace) -> int:
    # We don't strictly need cfg for list, but load it so a bad path fails
    # fast and consistently with the mutating subcommands.
    load_config(Path(args.config))
    entries = list_delegates(args.mailbox)
    if args.json:
        emit_json_lines([asdict(e) for e in entries])
        return 0
    if not entries:
        print(f"(no delegates on {args.mailbox})")
        return 0
    # Human table: kind, delegate, access_rights, deny.
    header = f"{'KIND':<14} {'DELEGATE':<40} {'ACCESS_RIGHTS':<20} DENY"
    print(header)
    print("-" * len(header))
    for e in entries:
        print(
            f"{e.kind:<14} {e.delegate:<40} {e.access_rights:<20} {e.deny}"
        )
    return 0


def _run_mutation(
    args: argparse.Namespace, *, action: str,
) -> int:
    if not args.confirm:
        print(
            f"(dry-run) would {action.lower()} {args.rights!r} on "
            f"{args.mailbox!r} for {args.delegate!r}; "
            f"re-run with --confirm to apply.",
            file=sys.stderr,
        )
        return 2

    cfg = load_config(Path(args.config))
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    op = Operation(
        op_id=new_op_id(),
        action=f"mail.delegate.{action.lower()}",
        drive_id=args.mailbox,
        item_id=args.delegate,
        args={
            "mailbox": args.mailbox,
            "delegate": args.delegate,
            "access_rights": args.rights,
        },
    )
    executor = execute_grant if action == "Grant" else execute_revoke
    result = executor(op, logger, before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(
        f"[{op.op_id}] ok — {action.lower()} {args.rights!r} "
        f"{'to' if action == 'Grant' else 'from'} {args.delegate!r} "
        f"on {args.mailbox!r}"
    )
    return 0


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.subcommand == "list":
        return _run_list(args)
    if args.subcommand == "grant":
        return _run_mutation(args, action="Grant")
    if args.subcommand == "revoke":
        return _run_mutation(args, action="Revoke")
    return 2
