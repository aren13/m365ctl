"""`m365ctl mail snooze` — Deferred/<date> + Snooze/<date> category convention.

Two modes:

    mail snooze <message-id> --until <date-or-relative> --confirm
    mail snooze --process [--confirm]

The first defers a single message. The second walks ``Deferred/<YYYY-MM-DD>``
folders that are due (today or earlier) and moves their contents back to
Inbox while clearing the matching ``Snooze/<date>`` category.

Both modes compose existing ``mail.move`` + ``mail.categorize`` ops; the
existing Phase 10 executor table dispatches them.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from m365ctl.common.audit import AuditLogger
from m365ctl.common.config import AuthMode, Config
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation
from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.convenience.snooze import (
    SnoozeError,
    build_snooze_ops,
    build_unsnooze_ops,
    find_due_snoozed,
    parse_until,
)
from m365ctl.mail.mutate._common import derive_mailbox_upn
from m365ctl.mail.triage.runner import _EXECUTORS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="m365ctl mail snooze",
        description=(
            "Snooze a message into Deferred/<date> + Snooze/<date>, or "
            "--process due Deferred folders back into Inbox."
        ),
    )
    add_common_args(p)
    p.add_argument(
        "message_id", nargs="?",
        help="Graph message id; required unless --process.",
    )
    p.add_argument(
        "--until",
        help="Target date: YYYY-MM-DD or '5d' / '24h'. Required without --process.",
    )
    p.add_argument(
        "--process", action="store_true",
        help="Walk Deferred/<YYYY-MM-DD> folders due today-or-earlier and "
             "move their messages back to Inbox.",
    )
    p.add_argument(
        "--confirm", action="store_true",
        help="Required to perform mutations.",
    )
    return p


def _list_folder_paths(catalog_path: Path, mailbox_upn: str) -> list[str]:
    if not catalog_path.exists():
        return []
    with open_catalog(catalog_path) as conn:
        cur = conn.execute(
            "SELECT path FROM mail_folders WHERE mailbox_upn = ?",
            [mailbox_upn],
        )
        return [r[0] for r in cur.fetchall() if r[0]]


def _list_messages_in_folder(
    catalog_path: Path, mailbox_upn: str, folder_path: str,
) -> list[dict[str, Any]]:
    if not catalog_path.exists():
        return []
    with open_catalog(catalog_path) as conn:
        cur = conn.execute(
            """
            SELECT message_id, categories
            FROM mail_messages
            WHERE mailbox_upn = ?
              AND parent_folder_path = ?
              AND COALESCE(is_deleted, false) = false
            """,
            [mailbox_upn, folder_path],
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _dispatch(
    ops: list[Operation],
    *,
    cfg: Config,
    mailbox_spec: str,
    auth_mode: AuthMode,
    graph: GraphClient,
    logger: AuditLogger,
) -> tuple[int, int]:
    ok = 0
    bad = 0
    for op in ops:
        executor = _EXECUTORS.get(op.action)
        if executor is None:
            print(f"error: no executor for action {op.action!r}",
                  file=sys.stderr)
            bad += 1
            continue
        try:
            r = executor(
                op, cfg=cfg, mailbox_spec=mailbox_spec,
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
    return ok, bad


def _run_until(args: argparse.Namespace) -> int:
    if not args.message_id:
        print("mail snooze: <message-id> required without --process",
              file=sys.stderr)
        return 2
    if not args.until:
        print("mail snooze: --until required without --process",
              file=sys.stderr)
        return 2
    if not args.confirm:
        print(
            f"(dry-run) would snooze {args.message_id} until {args.until}. "
            f"Re-run with --confirm.",
            file=sys.stderr,
        )
        return 2
    try:
        due = parse_until(args.until, now=datetime.now(timezone.utc))
    except SnoozeError as e:
        print(f"mail snooze: {e}", file=sys.stderr)
        return 2

    cfg, auth_mode, cred = load_and_authorize(args)
    mailbox_upn = derive_mailbox_upn(args.mailbox)
    ops = build_snooze_ops(args.message_id, due, mailbox_upn)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    ok, bad = _dispatch(
        ops, cfg=cfg, mailbox_spec=args.mailbox,
        auth_mode=auth_mode, graph=graph, logger=logger,
    )
    print(f"snoozed {args.message_id} until {due.isoformat()}: "
          f"{ok} ok, {bad} error(s)")
    return 1 if bad else 0


def _run_process(args: argparse.Namespace) -> int:
    if not args.confirm:
        print(
            "(dry-run) would process due Deferred/<date> folders. "
            "Re-run with --confirm.",
            file=sys.stderr,
        )
        return 2
    cfg, auth_mode, cred = load_and_authorize(args)
    mailbox_upn = derive_mailbox_upn(args.mailbox)
    folder_paths = _list_folder_paths(cfg.mail.catalog_path, mailbox_upn)
    today = datetime.now(timezone.utc).date()
    due_folders = find_due_snoozed(folder_paths, today=today)
    if not due_folders:
        print("(no due Deferred folders)")
        return 0

    ops: list[Operation] = []
    for folder_path, due_date in due_folders:
        msgs = _list_messages_in_folder(
            cfg.mail.catalog_path, mailbox_upn, folder_path,
        )
        for m in msgs:
            current = [
                c.strip()
                for c in (m.get("categories") or "").split(",")
                if c.strip()
            ]
            ops.extend(build_unsnooze_ops(
                m["message_id"],
                due_date=due_date,
                mailbox_upn=mailbox_upn,
                current_categories=current,
            ))

    if not ops:
        print(f"(no messages in {len(due_folders)} due folder(s))")
        return 0

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    ok, bad = _dispatch(
        ops, cfg=cfg, mailbox_spec=args.mailbox,
        auth_mode=auth_mode, graph=graph, logger=logger,
    )
    print(
        f"processed {len(due_folders)} due folder(s), "
        f"{len(ops)} op(s): {ok} ok, {bad} error(s)"
    )
    return 1 if bad else 0


def _today() -> date:  # exposed for tests; safe default
    return datetime.now(timezone.utc).date()


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.process:
        return _run_process(args)
    return _run_until(args)


__all__ = ["main", "build_parser"]
