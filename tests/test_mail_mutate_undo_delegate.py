"""Tests for inverse-op registration of mail.delegate.{grant,revoke}.

Each test seeds an audit start/end pair, calls
``build_reverse_mail_operation``, and asserts the returned reverse
``Operation`` has the expected action + args. Inverse is *not* executed
— only its shape is checked.
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


def test_inverse_of_delegate_grant_is_revoke(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    _seed(logger,
          op_id="op-grant-1", cmd="mail-delegate-grant",
          drive_id="", item_id="",
          args={"mailbox": "team@example.com",
                "delegate": "alice@example.com",
                "access_rights": "FullAccess"},
          before={},
          after={"action": "Grant",
                 "mailbox": "team@example.com",
                 "delegate": "alice@example.com",
                 "access_rights": "FullAccess"})
    rev = build_reverse_mail_operation(logger, "op-grant-1")
    assert rev.action == "mail.delegate.revoke"
    assert rev.args["mailbox"] == "team@example.com"
    assert rev.args["delegate"] == "alice@example.com"
    assert rev.args["access_rights"] == "FullAccess"


def test_inverse_of_delegate_revoke_is_grant(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    _seed(logger,
          op_id="op-revoke-1", cmd="mail-delegate-revoke",
          drive_id="", item_id="",
          args={"mailbox": "team@example.com",
                "delegate": "bob@example.com",
                "access_rights": "SendAs"},
          before={},
          after={"action": "Revoke",
                 "mailbox": "team@example.com",
                 "delegate": "bob@example.com",
                 "access_rights": "SendAs"})
    rev = build_reverse_mail_operation(logger, "op-revoke-1")
    assert rev.action == "mail.delegate.grant"
    assert rev.args["mailbox"] == "team@example.com"
    assert rev.args["delegate"] == "bob@example.com"
    assert rev.args["access_rights"] == "SendAs"
