"""Reverse-op tests for Phase 5a compose verbs."""
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


def _record(logger, *, op_id, cmd, drive_id, item_id, args, before, after):
    log_mutation_start(logger, op_id=op_id, cmd=cmd, args=args,
                       drive_id=drive_id, item_id=item_id, before=before)
    log_mutation_end(logger, op_id=op_id, after=after, result="ok")


def test_reverse_draft_create_emits_draft_delete(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-dc", cmd="mail-draft-create",
        drive_id="me", item_id="",
        args={"subject": "Hi", "body": "x", "to": ["a@example.com"]},
        before={},
        after={"id": "new-draft", "web_link": "x"},
    )
    rev = build_reverse_mail_operation(logger, "op-dc")
    assert rev.action == "mail.draft.delete"
    assert rev.item_id == "new-draft"


def test_reverse_draft_update_restores_prior(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-du", cmd="mail-draft-update",
        drive_id="me", item_id="d1",
        args={"subject": "New"},
        before={"subject": "Old", "body": {"contentType": "text", "content": "old body"}},
        after={"updated": True},
    )
    rev = build_reverse_mail_operation(logger, "op-du")
    assert rev.action == "mail.draft.update"
    assert rev.args["subject"] == "Old"


def test_reverse_draft_delete_emits_create_from_captured(tmp_path):
    logger = _logger(tmp_path)
    prior = {
        "subject": "Lost", "body": {"contentType": "text", "content": "body"},
        "toRecipients": [{"emailAddress": {"address": "a@example.com"}}],
    }
    _record(
        logger, op_id="op-dd", cmd="mail-draft-delete",
        drive_id="me", item_id="d1",
        args={}, before=prior, after=None,
    )
    rev = build_reverse_mail_operation(logger, "op-dd")
    assert rev.action == "mail.draft.create"
    assert rev.args["subject"] == "Lost"


def test_reverse_draft_delete_rejects_empty_before(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-bad", cmd="mail-draft-delete",
        drive_id="me", item_id="d1", args={}, before={}, after=None,
    )
    with pytest.raises(Irreversible):
        build_reverse_mail_operation(logger, "op-bad")


def test_reverse_attach_add_emits_remove(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-aa", cmd="mail-attach-add",
        drive_id="me", item_id="m1",
        args={"name": "x"},
        before={},
        after={"id": "att-1", "name": "x", "size": 10, "content_hash": "h"},
    )
    rev = build_reverse_mail_operation(logger, "op-aa")
    assert rev.action == "mail.attach.remove"
    assert rev.args["attachment_id"] == "att-1"


def test_reverse_attach_remove_emits_add_from_captured(tmp_path):
    logger = _logger(tmp_path)
    prior = {
        "id": "att-1", "name": "report.pdf",
        "content_type": "application/pdf", "size": 100,
        "content_bytes_b64": "ZGF0YQ==",
    }
    _record(
        logger, op_id="op-ar", cmd="mail-attach-remove",
        drive_id="me", item_id="m1",
        args={"attachment_id": "att-1"}, before=prior, after=None,
    )
    rev = build_reverse_mail_operation(logger, "op-ar")
    assert rev.action == "mail.attach.add"
    assert rev.args["name"] == "report.pdf"
    assert rev.args["content_bytes_b64"] == "ZGF0YQ=="


def test_dispatcher_mail_send_is_irreversible():
    d = Dispatcher()
    register_mail_inverses(d)
    with pytest.raises(IrreversibleOp) as ei:
        d.build_inverse("mail.send", before={}, after={})
    assert "recalled" in str(ei.value).lower() or "cannot" in str(ei.value).lower()


def test_dispatcher_mail_reply_is_irreversible():
    d = Dispatcher()
    register_mail_inverses(d)
    with pytest.raises(IrreversibleOp):
        d.build_inverse("mail.reply", before={}, after={})


def test_dispatcher_mail_reply_all_is_irreversible():
    d = Dispatcher()
    register_mail_inverses(d)
    with pytest.raises(IrreversibleOp):
        d.build_inverse("mail.reply.all", before={}, after={})


def test_dispatcher_mail_forward_is_irreversible():
    d = Dispatcher()
    register_mail_inverses(d)
    with pytest.raises(IrreversibleOp):
        d.build_inverse("mail.forward", before={}, after={})


def test_dispatcher_registers_all_phase5a_reversibles():
    d = Dispatcher()
    register_mail_inverses(d)
    for action in (
        "mail.draft.create", "mail.draft.update", "mail.draft.delete",
        "mail.attach.add", "mail.attach.remove",
    ):
        assert d.is_registered(action), f"missing reversible {action}"
