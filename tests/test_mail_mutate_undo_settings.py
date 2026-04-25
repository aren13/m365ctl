"""Tests for inverse-op registration of mail.settings.* verbs.

Each test exercises ``build_reverse_mail_operation`` with a synthetic
start/end record pair and asserts the returned reverse ``Operation`` has
the expected ``action`` + key args. The inverse is *not* executed —
only its shape is checked.
"""
from __future__ import annotations

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.mail.mutate.undo import build_reverse_mail_operation


def _seed(logger: AuditLogger, *, op_id: str, cmd: str,
          drive_id: str, item_id: str, args: dict, before: dict,
          after: dict | None, result: str = "ok") -> None:
    log_mutation_start(
        logger, op_id=op_id, cmd=cmd, args=args,
        drive_id=drive_id, item_id=item_id, before=before,
    )
    log_mutation_end(logger, op_id=op_id, after=after, result=result)


def test_inverse_of_set_timezone_restores_prior_tz(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    _seed(logger,
          op_id="op-tz-1", cmd="mail-settings-timezone",
          drive_id="me", item_id="",
          args={"mailbox_spec": "me", "auth_mode": "delegated",
                "timezone": "Europe/Istanbul"},
          before={"timeZone": "Turkey Standard Time"},
          after={"timeZone": "Europe/Istanbul"})
    rev = build_reverse_mail_operation(logger, "op-tz-1")
    assert rev.action == "mail.settings.timezone"
    assert rev.args["timezone"] == "Turkey Standard Time"
    assert rev.args["mailbox_spec"] == "me"
    assert rev.args["auth_mode"] == "delegated"


def test_inverse_of_set_working_hours_restores_prior_dict(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    prior_wh = {
        "daysOfWeek": ["monday", "tuesday", "wednesday", "thursday", "friday"],
        "startTime": "08:00:00",
        "endTime": "16:00:00",
        "timeZone": {"name": "UTC"},
    }
    new_wh = {
        "daysOfWeek": ["monday", "tuesday", "wednesday", "thursday", "friday"],
        "startTime": "09:00:00",
        "endTime": "17:00:00",
        "timeZone": {"name": "Europe/Istanbul"},
    }
    _seed(logger,
          op_id="op-wh-1", cmd="mail-settings-working-hours",
          drive_id="me", item_id="",
          args={"mailbox_spec": "me", "auth_mode": "delegated",
                "working_hours": new_wh},
          before={"workingHours": prior_wh},
          after={})
    rev = build_reverse_mail_operation(logger, "op-wh-1")
    assert rev.action == "mail.settings.working-hours"
    assert rev.args["working_hours"] == prior_wh


def test_inverse_of_set_auto_reply_restores_with_force(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    prior_ar = {
        "status": "scheduled",
        "scheduledStartDateTime": {"dateTime": "2026-01-01T00:00:00", "timeZone": "UTC"},
        "scheduledEndDateTime": {"dateTime": "2026-04-01T00:00:00", "timeZone": "UTC"},
        "internalReplyMessage": "old",
        "externalReplyMessage": "old",
        "externalAudience": "all",
    }
    _seed(logger,
          op_id="op-ar-1", cmd="mail-settings-auto-reply",
          drive_id="me", item_id="",
          args={"mailbox_spec": "me", "auth_mode": "delegated",
                "auto_reply": {"status": "disabled"}},
          before={"automaticRepliesSetting": prior_ar},
          after={})
    rev = build_reverse_mail_operation(logger, "op-ar-1")
    assert rev.action == "mail.settings.auto-reply"
    assert rev.args["auto_reply"] == prior_ar
    # `force=True` so the safety gate doesn't reject restoring a long OOO.
    assert rev.args["force"] is True
