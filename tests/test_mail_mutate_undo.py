"""Tests for m365ctl.mail.mutate.undo — reverse-op builder + Dispatcher wiring."""
from __future__ import annotations

from pathlib import Path

import pytest

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.undo import Dispatcher, IrreversibleOp
from m365ctl.mail.mutate.undo import (
    build_reverse_mail_operation,
    register_mail_inverses,
)
from m365ctl.onedrive.mutate.undo import Irreversible


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def _record_mutation(logger, *, op_id, cmd, drive_id, item_id, args, before, after):
    log_mutation_start(logger, op_id=op_id, cmd=cmd, args=args,
                       drive_id=drive_id, item_id=item_id, before=before)
    log_mutation_end(logger, op_id=op_id, after=after, result="ok")


# ---- folder inverses -------------------------------------------------------

def test_reverse_mail_folder_create_emits_delete(tmp_path):
    logger = _logger(tmp_path)
    _record_mutation(
        logger, op_id="op-1", cmd="mail-folder-create",
        drive_id="me", item_id="inbox",
        args={"name": "Triage", "parent_path": "/Inbox"},
        before={},
        after={"id": "new-folder", "path": "/Inbox/Triage"},
    )
    rev = build_reverse_mail_operation(logger, "op-1")
    assert rev.action == "mail.folder.delete"
    assert rev.drive_id == "me"
    assert rev.item_id == "new-folder"


def test_reverse_mail_folder_rename_emits_rename_back(tmp_path):
    logger = _logger(tmp_path)
    _record_mutation(
        logger, op_id="op-2", cmd="mail-folder-rename",
        drive_id="me", item_id="f1",
        args={"new_name": "Triaged"},
        before={"display_name": "Triage", "path": "/Inbox/Triage"},
        after={"display_name": "Triaged"},
    )
    rev = build_reverse_mail_operation(logger, "op-2")
    assert rev.action == "mail.folder.rename"
    assert rev.args == {"new_name": "Triage"}


def test_reverse_mail_folder_move_emits_move_back(tmp_path):
    logger = _logger(tmp_path)
    _record_mutation(
        logger, op_id="op-3", cmd="mail-folder-move",
        drive_id="me", item_id="f1",
        args={"destination_id": "archive", "destination_path": "/Archive"},
        before={"parent_id": "inbox", "path": "/Inbox/Triage"},
        after={"parent_id": "archive", "path": "/Archive"},
    )
    rev = build_reverse_mail_operation(logger, "op-3")
    assert rev.action == "mail.folder.move"
    assert rev.args["destination_id"] == "inbox"


def test_reverse_mail_folder_delete_is_irreversible(tmp_path):
    logger = _logger(tmp_path)
    _record_mutation(
        logger, op_id="op-4", cmd="mail-folder-delete",
        drive_id="me", item_id="f1",
        args={}, before={"display_name": "Triage"}, after=None,
    )
    with pytest.raises(Irreversible):
        build_reverse_mail_operation(logger, "op-4")


# ---- category inverses -----------------------------------------------------

def test_reverse_mail_categories_add_emits_remove(tmp_path):
    logger = _logger(tmp_path)
    _record_mutation(
        logger, op_id="op-5", cmd="mail-categories-add",
        drive_id="me", item_id="",
        args={"name": "Waiting", "color": "preset0"},
        before={},
        after={"id": "c-new", "display_name": "Waiting", "color": "preset0"},
    )
    rev = build_reverse_mail_operation(logger, "op-5")
    assert rev.action == "mail.categories.remove"
    assert rev.item_id == "c-new"


def test_reverse_mail_categories_update_emits_update_back(tmp_path):
    logger = _logger(tmp_path)
    _record_mutation(
        logger, op_id="op-6", cmd="mail-categories-update",
        drive_id="me", item_id="c1",
        args={"name": "Waiting-New", "color": "preset2"},
        before={"display_name": "Waiting", "color": "preset0"},
        after={"display_name": "Waiting-New", "color": "preset2"},
    )
    rev = build_reverse_mail_operation(logger, "op-6")
    assert rev.action == "mail.categories.update"
    assert rev.args == {"name": "Waiting", "color": "preset0"}


def test_reverse_mail_categories_remove_emits_add(tmp_path):
    logger = _logger(tmp_path)
    _record_mutation(
        logger, op_id="op-7", cmd="mail-categories-remove",
        drive_id="me", item_id="c1",
        args={},
        before={"display_name": "Waiting", "color": "preset0"},
        after=None,
    )
    rev = build_reverse_mail_operation(logger, "op-7")
    assert rev.action == "mail.categories.add"
    assert rev.args == {"name": "Waiting", "color": "preset0"}


# ---- failed-original rejection --------------------------------------------

def test_reverse_rejects_original_non_ok(tmp_path):
    logger = _logger(tmp_path)
    log_mutation_start(logger, op_id="op-bad", cmd="mail-folder-create",
                       args={"name": "X"}, drive_id="me", item_id="inbox", before={})
    log_mutation_end(logger, op_id="op-bad", after=None, result="error", error="conflict")
    with pytest.raises(Irreversible):
        build_reverse_mail_operation(logger, "op-bad")


# ---- Dispatcher registration ----------------------------------------------

def test_register_mail_inverses_registers_all_phase_2_verbs():
    d = Dispatcher()
    register_mail_inverses(d)
    for action in (
        "mail.folder.create", "mail.folder.rename", "mail.folder.move",
        "mail.categories.add", "mail.categories.update", "mail.categories.remove",
    ):
        assert d.is_registered(action), f"missing reversible registration for {action}"
    assert d.is_registered("mail.folder.delete")
    with pytest.raises(IrreversibleOp):
        d.build_inverse("mail.folder.delete", before={}, after={})
