"""Phase 13: mail.send.as is registered as irreversible."""
from __future__ import annotations

import pytest

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.mail.mutate.undo import build_reverse_mail_operation
from m365ctl.onedrive.mutate.undo import Irreversible


def test_inverse_of_send_as_raises_irreversible(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    log_mutation_start(
        logger,
        op_id="op-sa-1",
        cmd="mail-sendas",
        args={
            "from_upn": "bob@example.com",
            "subject": "hi",
            "body": "x",
            "to": ["alice@example.com"],
            "authenticated_principal": "11111111",
        },
        drive_id="bob@example.com",
        item_id="",
        before={},
    )
    log_mutation_end(
        logger,
        op_id="op-sa-1",
        after={"sent_at": "2026-04-25T00:00:00+00:00",
               "effective_sender": "bob@example.com",
               "authenticated_principal": "11111111"},
        result="ok",
    )
    with pytest.raises(Irreversible) as ei:
        build_reverse_mail_operation(logger, "op-sa-1")
    msg = str(ei.value)
    assert "irreversible" in msg.lower() or "delivered" in msg.lower() \
        or "effective_sender" in msg or "authenticated_principal" in msg
