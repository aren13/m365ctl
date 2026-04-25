"""`m365ctl mail archive` — bulk-move-by-month convenience.

Builds a Plan of ``mail.move`` ops landing each qualifying message into
``Archive/<YYYY>/<MM>``. With ``--plan-out`` writes the plan to disk and exits
(dry run). With ``--confirm`` dispatches each op via the existing Phase 10
executor table — same audit/undo flow as the triage runner.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import write_plan
from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.convenience.archive import build_archive_plan
from m365ctl.mail.mutate._common import derive_mailbox_upn
from m365ctl.mail.triage.runner import _EXECUTORS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="m365ctl mail archive",
        description=(
            "Bulk-move messages older than --older-than-days from --folder "
            "into Archive/<YYYY>/<MM>. Dry-run with --plan-out, execute with "
            "--confirm."
        ),
    )
    add_common_args(p)
    p.add_argument(
        "--older-than-days", type=int, required=True,
        help="Only consider messages with received_at older than N days.",
    )
    p.add_argument(
        "--folder", required=True,
        help="Source folder path (e.g. 'Inbox').",
    )
    p.add_argument(
        "--plan-out",
        help="Write the plan to this path and exit (dry run).",
    )
    p.add_argument(
        "--confirm", action="store_true",
        help="Execute the plan via the existing mail.move executor.",
    )
    return p


def _load_rows(catalog_path: Path, mailbox_upn: str) -> list[dict]:
    if not catalog_path.exists():
        return []
    with open_catalog(catalog_path) as conn:
        cur = conn.execute(
            """
            SELECT message_id, subject, from_address, received_at,
                   parent_folder_path
            FROM mail_messages
            WHERE mailbox_upn = ?
              AND COALESCE(is_deleted, false) = false
            """,
            [mailbox_upn],
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if not args.plan_out and not args.confirm:
        print(
            "error: provide --plan-out (dry run) or --confirm (execute)",
            file=sys.stderr,
        )
        return 2
    if args.plan_out and args.confirm:
        print(
            "error: --plan-out and --confirm are mutually exclusive",
            file=sys.stderr,
        )
        return 2

    cfg, auth_mode, cred = load_and_authorize(args)
    mailbox_upn = derive_mailbox_upn(args.mailbox)
    now = datetime.now(timezone.utc)

    rows = _load_rows(cfg.mail.catalog_path, mailbox_upn)
    source_cmd = (
        f"mail archive --older-than-days {args.older_than_days} "
        f"--folder {args.folder}"
    )
    plan = build_archive_plan(
        rows,
        older_than_days=args.older_than_days,
        folder=args.folder,
        mailbox_upn=mailbox_upn,
        source_cmd=source_cmd,
        scope=args.mailbox,
        now=now,
    )

    if args.plan_out:
        write_plan(plan, Path(args.plan_out))
        print(
            f"plan: {len(plan.operations)} mail.move op(s) -> {args.plan_out}"
        )
        return 0

    # --confirm path: dispatch each op via the existing executor table.
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    ok = 0
    bad = 0
    for op in plan.operations:
        executor = _EXECUTORS.get(op.action)
        if executor is None:
            print(
                f"error: no executor for action {op.action!r}",
                file=sys.stderr,
            )
            bad += 1
            continue
        try:
            r = executor(
                op, cfg=cfg, mailbox_spec=args.mailbox,
                auth_mode=auth_mode, graph=graph, logger=logger,
            )
        except Exception as e:
            print(f"[{op.op_id}] error: {e}", file=sys.stderr)
            bad += 1
            continue
        if getattr(r, "status", "") == "ok":
            ok += 1
        else:
            bad += 1
            err = getattr(r, "error", "(unknown)")
            print(f"[{op.op_id}] error: {err}", file=sys.stderr)
    print(f"archived: {ok} ok, {bad} error(s)")
    return 1 if bad else 0


__all__ = ["main", "build_parser"]
