"""`m365ctl mail sendas` — send mail as another mailbox (app-only, irreversible).

Phase 13: forces app-only routing via `POST /users/{from_upn}/sendMail`.
The audit log records both the effective sender (the mailbox being sent
as) and the authenticated principal (the app `client_id`) for compliance.

Out-of-scope from-UPNs require ``--unsafe-scope`` plus a TTY confirmation,
reusing the existing ``assert_mailbox_allowed`` flow. Send-as is
irreversible by definition — sent mail cannot be recalled programmatically.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.auth import AppOnlyCredential
from m365ctl.common.config import load_config
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.common.safety import ScopeViolation, assert_mailbox_allowed
from m365ctl.mail.mutate.send import execute_send_as


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="m365ctl mail sendas",
        description=(
            "Send mail as another mailbox via app-only "
            "POST /users/{from_upn}/sendMail. IRREVERSIBLE."
        ),
    )
    p.add_argument(
        "--config", default="config.toml",
        help="Path to config.toml (default: config.toml).",
    )
    p.add_argument(
        "from_upn",
        help="Bare UPN of the mailbox to send as (no upn:/shared: prefix).",
    )
    p.add_argument("--to", action="append", default=[], required=False,
                   help="Recipient (repeatable).")
    p.add_argument("--cc", action="append", default=[],
                   help="CC recipient (repeatable).")
    p.add_argument("--bcc", action="append", default=[],
                   help="BCC recipient (repeatable).")
    p.add_argument("--subject", required=True)
    p.add_argument("--body")
    p.add_argument("--body-file")
    p.add_argument("--body-type", choices=("text", "html"), default="text")
    p.add_argument("--importance", choices=("low", "normal", "high"))
    p.add_argument(
        "--unsafe-scope", action="store_true",
        help="Allow from_upn outside scope.allow_mailboxes (TTY confirm required).",
    )
    p.add_argument(
        "--confirm", action="store_true",
        help="Required to actually send (sendas is IRREVERSIBLE).",
    )
    return p


def _read_body(args: argparse.Namespace) -> str:
    if args.body_file:
        return Path(args.body_file).read_text()
    return args.body or ""


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if not args.confirm:
        print(
            f"(dry-run) would send-as {args.from_upn!r} "
            f"to={args.to} subject={args.subject!r}; "
            f"re-run with --confirm to send (IRREVERSIBLE).",
            file=sys.stderr,
        )
        return 2

    if not args.to:
        print("mail sendas: --to is required.", file=sys.stderr)
        return 2

    cfg = load_config(Path(args.config))

    try:
        assert_mailbox_allowed(
            f"upn:{args.from_upn}", cfg,
            auth_mode="app-only", unsafe_scope=args.unsafe_scope,
        )
    except ScopeViolation as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    body = _read_body(args)

    cred = AppOnlyCredential(cfg)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    op = Operation(
        op_id=new_op_id(),
        action="mail.send.as",
        drive_id=args.from_upn,
        item_id="",
        args={
            "from_upn": args.from_upn,
            "subject": args.subject,
            "body": body,
            "body_type": args.body_type,
            "to": list(args.to),
            "cc": list(args.cc),
            "bcc": list(args.bcc),
            "importance": args.importance,
            "authenticated_principal": cfg.client_id,
        },
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_send_as(op, graph, logger, before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(
        f"[{op.op_id}] ok — sent as {args.from_upn!r} "
        f"(authenticated principal: {cfg.client_id})"
    )
    return 0
