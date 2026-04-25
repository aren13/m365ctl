from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from m365ctl.common.audit import AuditLogger
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.mutate.settings import (
    execute_set_auto_reply,
    execute_set_timezone,
    execute_set_working_hours,
    OOOTooLong,
)


def _op(action: str, args: dict) -> Operation:
    return Operation(
        op_id=new_op_id(),
        action=action,
        drive_id="me",
        item_id="",
        args=args,
        dry_run_result="",
    )


def test_set_timezone_patches_graph(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"timeZone": "Europe/Istanbul"}
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.settings.timezone", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "timezone": "Europe/Istanbul",
    })
    r = execute_set_timezone(op, graph, logger, before={"timeZone": "Turkey Standard Time"})
    assert r.status == "ok"
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"timeZone": "Europe/Istanbul"}


def test_set_working_hours_patches(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {}
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    body = {
        "daysOfWeek": ["monday", "tuesday", "wednesday", "thursday", "friday"],
        "startTime": "09:00:00",
        "endTime": "17:00:00",
        "timeZone": {"name": "Europe/Istanbul"},
    }
    op = _op("mail.settings.working-hours", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "working_hours": body,
    })
    r = execute_set_working_hours(op, graph, logger, before={})
    assert r.status == "ok"
    sent = graph.patch.call_args.kwargs["json_body"]
    assert sent == {"workingHours": body}


def test_set_auto_reply_disabled(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {}
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.settings.auto-reply", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "auto_reply": {"status": "disabled"},
    })
    r = execute_set_auto_reply(op, graph, logger, before={})
    assert r.status == "ok"
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"automaticRepliesSetting": {"status": "disabled"}}


def test_set_auto_reply_scheduled_short_ok(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {}
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=10)
    op = _op("mail.settings.auto-reply", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "auto_reply": {
            "status": "scheduled",
            "scheduledStartDateTime": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "scheduledEndDateTime": {"dateTime": end.isoformat(), "timeZone": "UTC"},
            "internalReplyMessage": "OOO short",
            "externalReplyMessage": "OOO short",
            "externalAudience": "all",
        },
    })
    r = execute_set_auto_reply(op, graph, logger, before={})
    assert r.status == "ok"


def test_set_auto_reply_scheduled_too_long_raises(tmp_path):
    graph = MagicMock()
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=61)   # > 60
    op = _op("mail.settings.auto-reply", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "auto_reply": {
            "status": "scheduled",
            "scheduledStartDateTime": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "scheduledEndDateTime": {"dateTime": end.isoformat(), "timeZone": "UTC"},
            "internalReplyMessage": "x",
            "externalReplyMessage": "x",
            "externalAudience": "all",
        },
    })
    with pytest.raises(OOOTooLong, match="61"):
        execute_set_auto_reply(op, graph, logger, before={})


def test_executor_propagates_graph_error_as_status(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.patch.side_effect = GraphError("InvalidRequest: bad timezone")
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.settings.timezone", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "timezone": "Not-A-Zone",
    })
    r = execute_set_timezone(op, graph, logger, before={})
    assert r.status == "error"
    assert "InvalidRequest" in (r.error or "")
