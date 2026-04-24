"""Reverse-op tests for mail-delete-soft + closed mail.copy chain."""
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


def test_reverse_mail_delete_soft_emits_move_back(tmp_path):
    """Undo of mail-delete-soft = move back to before.parent_folder_id."""
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-del", cmd="mail-delete-soft",
        drive_id="me", item_id="m1",
        args={},
        before={"parent_folder_id": "inbox", "parent_folder_path": "/Inbox"},
        after={"parent_folder_id": "deleteditems-id", "deleted_from": "inbox"},
    )
    rev = build_reverse_mail_operation(logger, "op-del")
    assert rev.action == "mail.move"
    assert rev.drive_id == "me"
    assert rev.item_id == "m1"
    assert rev.args["destination_id"] == "inbox"
    assert rev.args.get("destination_path") == "/Inbox"


def test_reverse_mail_delete_soft_rejects_missing_before_parent(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-bad", cmd="mail-delete-soft",
        drive_id="me", item_id="m1",
        args={},
        before={},
        after={"parent_folder_id": "deleteditems-id"},
    )
    with pytest.raises(Irreversible):
        build_reverse_mail_operation(logger, "op-bad")


def test_dispatcher_mail_delete_soft_inverse_returns_move_back():
    """Dispatcher's inverse for mail.delete.soft is now a real (before, after) -> move-back spec."""
    d = Dispatcher()
    register_mail_inverses(d)
    inv = d.build_inverse(
        "mail.delete.soft",
        before={"parent_folder_id": "inbox", "parent_folder_path": "/Inbox"},
        after={"parent_folder_id": "deleteditems-id"},
    )
    assert inv["action"] == "mail.move"
    assert inv["args"]["destination_id"] == "inbox"


def test_dispatcher_mail_copy_inverse_chains_to_delete_soft():
    """mail.copy inverse → mail.delete.soft (unchanged from Phase 3)."""
    d = Dispatcher()
    register_mail_inverses(d)
    inv = d.build_inverse("mail.copy", before={}, after={"new_message_id": "m1-copy"})
    assert inv["action"] == "mail.delete.soft"
