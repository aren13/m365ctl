"""`m365ctl mail settings show|ooo|timezone|working-hours`.

Phase 1 (readers): ``show`` + ``ooo`` printer.
Phase 9 G3.1 (setters): ``timezone <tz> --confirm`` and
``working-hours --from-file <yaml> --confirm``.

The ``ooo`` printer remains for backward-compat; the standalone
``mail ooo`` dispatcher (Phase 9 G3.2) supersedes it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.cli._common import add_common_args, emit_json_lines, load_and_authorize
from m365ctl.mail.mutate._common import derive_mailbox_upn
from m365ctl.mail.mutate.settings import (
    execute_set_timezone,
    execute_set_working_hours,
)
from m365ctl.mail.settings import get_auto_reply, get_settings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail settings")
    add_common_args(p)
    sub = p.add_subparsers(dest="subcommand", required=True)

    sub.add_parser("show", help="Print all mailbox settings.")
    sub.add_parser("ooo", help="Print the automatic-replies setting.")

    tz = sub.add_parser("timezone", help="Set mailbox timezone (Olson or Windows).")
    tz.add_argument("timezone", help="Timezone name, e.g. 'Europe/Istanbul'.")
    tz.add_argument("--confirm", action="store_true")

    wh = sub.add_parser("working-hours", help="Set workingHours from a YAML file.")
    wh.add_argument("--from-file", dest="from_file", required=True,
                    help="Path to a YAML file with days_of_week/start_time/end_time/time_zone.")
    wh.add_argument("--confirm", action="store_true")

    return p


# ---- handlers --------------------------------------------------------------

def _run_show(args: argparse.Namespace) -> int:
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    s = get_settings(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)
    if args.json:
        emit_json_lines([s])
    else:
        print(f"timezone:       {s.timezone}")
        print(f"language:       {s.language.locale} ({s.language.display_name})")
        wh = s.working_hours
        days_str = ",".join(wh.days)
        print(f"working_hours:  {days_str} {wh.start_time}-{wh.end_time} {wh.time_zone}")
        print(f"auto_reply:     {s.auto_reply.status} (audience: {s.auto_reply.external_audience})")
        print(f"delegate_msgs:  {s.delegate_meeting_message_delivery}")
        print(f"date_format:    {s.date_format}")
        print(f"time_format:    {s.time_format}")
    return 0


def _run_ooo_print(args: argparse.Namespace) -> int:
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    ar = get_auto_reply(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)
    if args.json:
        emit_json_lines([ar])
    else:
        print(f"status:             {ar.status}")
        print(f"external_audience:  {ar.external_audience}")
        print(f"scheduled_start:    {ar.scheduled_start}")
        print(f"scheduled_end:      {ar.scheduled_end}")
        print(f"internal_reply:     {ar.internal_reply_message!r}")
        print(f"external_reply:     {ar.external_reply_message!r}")
    return 0


def _run_timezone(args: argparse.Namespace) -> int:
    if not args.confirm:
        print(
            f"(dry-run) would set timezone to {args.timezone!r}; "
            f"re-run with --confirm to apply.",
            file=sys.stderr,
        )
        return 0
    cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    op = Operation(
        op_id=new_op_id(),
        action="mail.settings.timezone",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id="",
        args={
            "mailbox_spec": args.mailbox,
            "auth_mode": auth_mode,
            "timezone": args.timezone,
        },
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_set_timezone(op, graph, logger, before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — timezone set to {args.timezone!r}")
    return 0


_REQUIRED_WH_FIELDS = ("days_of_week", "start_time", "end_time", "time_zone")


def _yaml_to_working_hours(doc: Any) -> dict[str, Any]:
    """Translate the user-facing YAML doc to the Graph workingHours body.

    Raises ``ValueError`` on missing or wrong-typed required fields.
    """
    if not isinstance(doc, dict):
        raise ValueError("working-hours YAML must be a mapping")
    missing = [f for f in _REQUIRED_WH_FIELDS if f not in doc]
    if missing:
        raise ValueError(f"working-hours YAML missing required field(s): {', '.join(missing)}")
    days = doc["days_of_week"]
    if not isinstance(days, list) or not all(isinstance(d, str) for d in days):
        raise ValueError("days_of_week must be a list of strings")
    return {
        "daysOfWeek": list(days),
        "startTime": str(doc["start_time"]),
        "endTime": str(doc["end_time"]),
        "timeZone": {"name": str(doc["time_zone"])},
    }


def _run_working_hours(args: argparse.Namespace) -> int:
    try:
        doc = yaml.safe_load(Path(args.from_file).read_text())
        body = _yaml_to_working_hours(doc)
    except (OSError, yaml.YAMLError, ValueError) as e:
        print(f"mail settings working-hours: {e}", file=sys.stderr)
        return 2

    if not args.confirm:
        print(
            f"(dry-run) would set working hours from {args.from_file!r}; "
            f"re-run with --confirm to apply.",
            file=sys.stderr,
        )
        return 0

    cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    op = Operation(
        op_id=new_op_id(),
        action="mail.settings.working-hours",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id="",
        args={
            "mailbox_spec": args.mailbox,
            "auth_mode": auth_mode,
            "working_hours": body,
        },
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_set_working_hours(op, graph, logger, before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — working hours updated")
    return 0


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.subcommand == "show":
        return _run_show(args)
    if args.subcommand == "ooo":
        return _run_ooo_print(args)
    if args.subcommand == "timezone":
        return _run_timezone(args)
    if args.subcommand == "working-hours":
        return _run_working_hours(args)
    return 2
