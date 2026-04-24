"""Reverse-op tests for Phase 3 verbs (move/copy/flag/read/focus/categorize)."""
from __future__ import annotations

from pathlib import Path

import pytest

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.undo import Dispatcher
from m365ctl.mail.mutate.undo import (
    build_reverse_mail_operation,
    register_mail_inverses,
)
from m365ctl.onedrive.mutate.undo import Irreversible


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def _record(logger, *, op_id, cmd, drive_id, item_id, args, before, after):
    log_mutation_start(logger, op_id=op_id, cmd=cmd, args=args,
                       drive_id=drive_id, item_id=item_id, before=before)
    log_mutation_end(logger, op_id=op_id, after=after, result="ok")


def test_reverse_mail_move_emits_move_back(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-1", cmd="mail-move",
        drive_id="me", item_id="m1",
        args={"destination_id": "archive"},
        before={"parent_folder_id": "inbox", "parent_folder_path": "/Inbox"},
        after={"parent_folder_id": "archive"},
    )
    rev = build_reverse_mail_operation(logger, "op-1")
    assert rev.action == "mail.move"
    assert rev.args["destination_id"] == "inbox"


def test_reverse_mail_move_rejects_missing_before_parent(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-bad", cmd="mail-move",
        drive_id="me", item_id="m1",
        args={"destination_id": "archive"},
        before={}, after={"parent_folder_id": "archive"},
    )
    with pytest.raises(Irreversible):
        build_reverse_mail_operation(logger, "op-bad")


def test_reverse_mail_copy_emits_delete_soft(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-c", cmd="mail-copy",
        drive_id="me", item_id="m1",
        args={"destination_id": "archive"},
        before={},
        after={"new_message_id": "m1-copy", "destination_folder_id": "archive"},
    )
    rev = build_reverse_mail_operation(logger, "op-c")
    assert rev.action == "mail.delete.soft"
    assert rev.item_id == "m1-copy"


def test_reverse_mail_copy_rejects_missing_new_message_id(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-cbad", cmd="mail-copy",
        drive_id="me", item_id="m1",
        args={"destination_id": "archive"},
        before={}, after={},
    )
    with pytest.raises(Irreversible):
        build_reverse_mail_operation(logger, "op-cbad")


def test_reverse_mail_flag_restores_prior(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-f", cmd="mail-flag",
        drive_id="me", item_id="m1",
        args={"status": "flagged", "due_at": "2026-05-01T00:00:00Z"},
        before={"status": "notFlagged", "start_at": None, "due_at": None},
        after={"status": "flagged", "due_at": "2026-05-01T00:00:00Z"},
    )
    rev = build_reverse_mail_operation(logger, "op-f")
    assert rev.action == "mail.flag"
    assert rev.args["status"] == "notFlagged"


def test_reverse_mail_read_flips(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-r", cmd="mail-read",
        drive_id="me", item_id="m1",
        args={"is_read": True},
        before={"is_read": False}, after={"is_read": True},
    )
    rev = build_reverse_mail_operation(logger, "op-r")
    assert rev.action == "mail.read"
    assert rev.args["is_read"] is False


def test_reverse_mail_focus_restores_prior(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-fo", cmd="mail-focus",
        drive_id="me", item_id="m1",
        args={"inference_classification": "focused"},
        before={"inference_classification": "other"},
        after={"inference_classification": "focused"},
    )
    rev = build_reverse_mail_operation(logger, "op-fo")
    assert rev.action == "mail.focus"
    assert rev.args["inference_classification"] == "other"


def test_reverse_mail_categorize_restores_prior_list(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-cat", cmd="mail-categorize",
        drive_id="me", item_id="m1",
        args={"categories": ["Followup", "Waiting"]},
        before={"categories": ["Archived"]},
        after={"categories": ["Followup", "Waiting"]},
    )
    rev = build_reverse_mail_operation(logger, "op-cat")
    assert rev.action == "mail.categorize"
    assert rev.args["categories"] == ["Archived"]


def test_register_mail_inverses_includes_phase3_verbs():
    d = Dispatcher()
    register_mail_inverses(d)
    for action in (
        "mail.move", "mail.copy", "mail.flag", "mail.read",
        "mail.focus", "mail.categorize", "mail.delete.soft",
    ):
        assert d.is_registered(action), f"missing {action}"
