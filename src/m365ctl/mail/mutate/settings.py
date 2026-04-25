"""Mailbox settings mutators with audit + undo support.

Wraps the existing ``mail.settings.update_mailbox_settings`` PATCH path
in three executors:
  - ``execute_set_timezone``       (mailboxSettings.timeZone)
  - ``execute_set_working_hours``  (mailboxSettings.workingHours)
  - ``execute_set_auto_reply``     (mailboxSettings.automaticRepliesSetting)

Each writes one ``begin``/``end`` audit pair and returns
``SettingsResult(status, error, after)``.

OOO durations longer than 60 days raise ``OOOTooLong`` so the CLI can
intercept and require an explicit TTY confirm before re-dispatching with
the bypass flag.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone as _tz
from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.endpoints import AuthMode, user_base


_MAX_OOO_DAYS = 60


class OOOTooLong(RuntimeError):
    """Raised when scheduled OOO duration exceeds the safety threshold."""


@dataclass
class SettingsResult:
    op_id: str
    status: str  # "ok" | "error"
    error: str | None = None
    after: dict[str, Any] = field(default_factory=dict)


def _settings_path(mailbox_spec: str, auth_mode: str) -> str:
    mode: AuthMode = "app-only" if auth_mode == "app-only" else "delegated"
    ub = user_base(mailbox_spec, auth_mode=mode)
    return f"{ub}/mailboxSettings"


def _do(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    cmd: str,
    body: dict[str, Any],
    before: dict[str, Any],
) -> SettingsResult:
    path = _settings_path(op.args["mailbox_spec"], op.args["auth_mode"])
    log_mutation_start(
        logger, op_id=op.op_id, cmd=cmd, args=op.args,
        drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        after = graph.patch(path, json_body=body)
    except GraphError as e:
        log_mutation_end(
            logger, op_id=op.op_id, after=None, result="error", error=str(e),
        )
        return SettingsResult(op_id=op.op_id, status="error", error=str(e))
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return SettingsResult(op_id=op.op_id, status="ok", after=after)


def execute_set_timezone(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> SettingsResult:
    body: dict[str, Any] = {"timeZone": op.args["timezone"]}
    return _do(op, graph, logger, cmd="mail-settings-timezone", body=body, before=before)


def execute_set_working_hours(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> SettingsResult:
    # Backfill `before` with the prior workingHours dict if the caller didn't
    # supply it; needed by the inverse-builder so undo can restore.
    if not before.get("workingHours"):
        path = _settings_path(op.args["mailbox_spec"], op.args["auth_mode"])
        try:
            current = graph.get(path)
            prior = current.get("workingHours") if isinstance(current, dict) else None
            if isinstance(prior, dict) and prior:
                before = {**before, "workingHours": prior}
        except GraphError:
            # Couldn't fetch — leave before as-is; undo will fail loudly.
            pass

    body: dict[str, Any] = {"workingHours": op.args["working_hours"]}
    return _do(op, graph, logger, cmd="mail-settings-working-hours", body=body, before=before)


def execute_set_auto_reply(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> SettingsResult:
    ar = op.args["auto_reply"]
    if ar.get("status") == "scheduled" and not op.args.get("force"):
        days = _ooo_duration_days(ar)
        if days is not None and days > _MAX_OOO_DAYS:
            raise OOOTooLong(
                f"OOO duration is {days} days (>{_MAX_OOO_DAYS}); "
                f"set args['force'] = True to bypass"
            )
    body: dict[str, Any] = {"automaticRepliesSetting": ar}
    return _do(op, graph, logger, cmd="mail-settings-auto-reply", body=body, before=before)


def _ooo_duration_days(ar: dict[str, Any]) -> int | None:
    """Compute scheduled-end minus scheduled-start in days (rounded up)."""
    s = (ar.get("scheduledStartDateTime") or {}).get("dateTime")
    e = (ar.get("scheduledEndDateTime") or {}).get("dateTime")
    if not s or not e:
        return None
    s_dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    e_dt = datetime.fromisoformat(e.replace("Z", "+00:00"))
    if s_dt.tzinfo is None:
        s_dt = s_dt.replace(tzinfo=_tz.utc)
    if e_dt.tzinfo is None:
        e_dt = e_dt.replace(tzinfo=_tz.utc)
    seconds = (e_dt - s_dt).total_seconds()
    if seconds <= 0:
        return 0
    # Ceiling division so 60.5 days -> 61 (triggers safety gate).
    days = int((seconds + 86399) // 86400)
    return days
