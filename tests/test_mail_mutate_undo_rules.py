"""Tests for inverse-op registration of mail.rule.* verbs.

Each test exercises ``build_reverse_mail_operation`` (the audit-log-driven
reverse-builder) with a synthetic start/end record pair and asserts the
returned reverse ``Operation`` has the expected ``action`` + key args.
The inverse is *not* executed — only its shape is checked.
"""
from __future__ import annotations

from m365ctl.common.audit import AuditLogger
from m365ctl.mail.mutate.undo import build_reverse_mail_operation


def _seed(logger: AuditLogger, *, op_id: str, cmd: str,
          drive_id: str, item_id: str, args: dict, before: dict,
          after: dict | None, result: str = "ok") -> None:
    """Append a start + end record to the audit log."""
    from m365ctl.common.audit import log_mutation_end, log_mutation_start
    log_mutation_start(
        logger, op_id=op_id, cmd=cmd, args=args,
        drive_id=drive_id, item_id=item_id, before=before,
    )
    log_mutation_end(logger, op_id=op_id, after=after, result=result)


def test_inverse_of_create_is_delete_on_new_id(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    _seed(logger,
          op_id="op-create-1", cmd="mail-rule-create",
          drive_id="me", item_id="",
          args={"mailbox_spec": "me", "auth_mode": "delegated",
                "body": {"displayName": "r"}},
          before={},
          after={"id": "new-rule-1"})
    rev = build_reverse_mail_operation(logger, "op-create-1")
    assert rev.action == "mail.rule.delete"
    assert rev.args["rule_id"] == "new-rule-1"


def test_inverse_of_delete_is_create_with_before_body(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    before_body = {
        "id": "rule-A",
        "displayName": "r", "sequence": 10, "isEnabled": True,
        "conditions": {}, "actions": {"delete": True}, "exceptions": {},
    }
    _seed(logger,
          op_id="op-del-1", cmd="mail-rule-delete",
          drive_id="me", item_id="rule-A",
          args={"mailbox_spec": "me", "auth_mode": "delegated",
                "rule_id": "rule-A"},
          before=before_body,
          after=None)
    rev = build_reverse_mail_operation(logger, "op-del-1")
    assert rev.action == "mail.rule.create"
    # `id` must be stripped — Graph re-assigns on create.
    assert "id" not in rev.args["body"]
    assert rev.args["body"]["displayName"] == "r"
    assert rev.args["body"]["sequence"] == 10


def test_inverse_of_update_restores_before_body(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    before_body = {
        "id": "rule-A",
        "displayName": "before", "sequence": 5, "isEnabled": True,
    }
    _seed(logger,
          op_id="op-upd-1", cmd="mail-rule-update",
          drive_id="me", item_id="rule-A",
          args={"mailbox_spec": "me", "auth_mode": "delegated",
                "rule_id": "rule-A",
                "body": {"displayName": "after"}},
          before=before_body,
          after={"id": "rule-A"})
    rev = build_reverse_mail_operation(logger, "op-upd-1")
    assert rev.action == "mail.rule.update"
    assert rev.args["rule_id"] == "rule-A"
    assert "id" not in rev.args["body"]
    assert rev.args["body"]["displayName"] == "before"


def test_inverse_of_set_enabled_flips_back(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    _seed(logger,
          op_id="op-en-1", cmd="mail-rule-set-enabled",
          drive_id="me", item_id="rule-A",
          args={"mailbox_spec": "me", "auth_mode": "delegated",
                "rule_id": "rule-A", "is_enabled": False},
          before={"isEnabled": True},
          after={"id": "rule-A", "isEnabled": False})
    rev = build_reverse_mail_operation(logger, "op-en-1")
    assert rev.action == "mail.rule.set-enabled"
    assert rev.args["rule_id"] == "rule-A"
    assert rev.args["is_enabled"] is True


def test_inverse_of_reorder_uses_before_ordering(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    prior = [
        {"rule_id": "rule-A", "sequence": 1},
        {"rule_id": "rule-B", "sequence": 2},
    ]
    _seed(logger,
          op_id="op-ord-1", cmd="mail-rule-reorder",
          drive_id="me", item_id="",
          args={"mailbox_spec": "me", "auth_mode": "delegated",
                "ordering": [
                    {"rule_id": "rule-A", "sequence": 20},
                    {"rule_id": "rule-B", "sequence": 10},
                ]},
          before={"ordering": prior},
          after={"ordering": [
              {"rule_id": "rule-A", "sequence": 20},
              {"rule_id": "rule-B", "sequence": 10},
          ]})
    rev = build_reverse_mail_operation(logger, "op-ord-1")
    assert rev.action == "mail.rule.reorder"
    assert rev.args["ordering"] == prior
