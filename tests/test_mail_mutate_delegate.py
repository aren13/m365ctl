"""Tests for `m365ctl.mail.mutate.delegate`.

Patch `m365ctl.mail.mutate.delegate.invoke_pwsh` directly (it's imported at
module level into delegate.py) — we don't need to drill into subprocess.run.
"""
from __future__ import annotations

import json

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate import delegate as delegate_mod
from m365ctl.mail.mutate.delegate import (
    DelegateEntry,
    execute_grant,
    execute_revoke,
    list_delegates,
)


# ---------------------------------------------------------------------------
# list_delegates
# ---------------------------------------------------------------------------

def test_list_delegates_invokes_pwsh_and_parses_jsonl(mocker):
    out = "\n".join([
        json.dumps({
            "kind": "FullAccess",
            "mailbox": "team@example.com",
            "delegate": "alice@example.com",
            "access_rights": "FullAccess",
            "deny": False,
        }),
        json.dumps({
            "kind": "SendAs",
            "mailbox": "team@example.com",
            "delegate": "bob@example.com",
            "access_rights": "SendAs",
            "deny": False,
        }),
        "",  # blank line tolerated
    ])
    mock_invoke = mocker.patch.object(
        delegate_mod, "invoke_pwsh",
        return_value=(0, out, ""),
    )

    entries = list_delegates("team@example.com")

    mock_invoke.assert_called_once()
    args = mock_invoke.call_args[0][1]
    assert args == ["-Mailbox", "team@example.com", "-Action", "List"]
    assert entries == [
        DelegateEntry(
            kind="FullAccess",
            mailbox="team@example.com",
            delegate="alice@example.com",
            access_rights="FullAccess",
            deny=False,
        ),
        DelegateEntry(
            kind="SendAs",
            mailbox="team@example.com",
            delegate="bob@example.com",
            access_rights="SendAs",
            deny=False,
        ),
    ]


# ---------------------------------------------------------------------------
# execute_grant happy path + audit
# ---------------------------------------------------------------------------

def test_execute_grant_invokes_pwsh_and_logs_audit(tmp_path, mocker):
    mock_invoke = mocker.patch.object(
        delegate_mod, "invoke_pwsh",
        return_value=(0, json.dumps({"status": "ok"}), ""),
    )

    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = Operation(
        op_id="op-grant-1", action="mail.delegate.grant",
        drive_id="", item_id="",
        args={
            "mailbox": "team@example.com",
            "delegate": "alice@example.com",
            "access_rights": "FullAccess",
        },
        dry_run_result="",
    )

    result = execute_grant(op, logger, before={})

    assert result.status == "ok"
    args = mock_invoke.call_args[0][1]
    assert args == [
        "-Mailbox", "team@example.com",
        "-Action", "Grant",
        "-Delegate", "alice@example.com",
        "-AccessRights", "FullAccess",
    ]
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-grant-1"]
    assert len(entries) == 2
    assert entries[0]["phase"] == "start"
    assert entries[0]["cmd"] == "mail-delegate-grant"
    assert entries[1]["phase"] == "end"
    assert entries[1]["result"] == "ok"


# ---------------------------------------------------------------------------
# execute_revoke happy path + audit
# ---------------------------------------------------------------------------

def test_execute_revoke_invokes_pwsh_and_logs_audit(tmp_path, mocker):
    mock_invoke = mocker.patch.object(
        delegate_mod, "invoke_pwsh",
        return_value=(0, json.dumps({"status": "ok"}), ""),
    )

    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = Operation(
        op_id="op-revoke-1", action="mail.delegate.revoke",
        drive_id="", item_id="",
        args={
            "mailbox": "team@example.com",
            "delegate": "alice@example.com",
            "access_rights": "SendAs",
        },
        dry_run_result="",
    )

    result = execute_revoke(op, logger, before={})

    assert result.status == "ok"
    args = mock_invoke.call_args[0][1]
    assert args == [
        "-Mailbox", "team@example.com",
        "-Action", "Revoke",
        "-Delegate", "alice@example.com",
        "-AccessRights", "SendAs",
    ]
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-revoke-1"]
    assert entries[0]["cmd"] == "mail-delegate-revoke"
    assert entries[-1]["result"] == "ok"


# ---------------------------------------------------------------------------
# Non-zero exit → error result
# ---------------------------------------------------------------------------

def test_execute_grant_returns_error_on_nonzero_exit(tmp_path, mocker):
    mocker.patch.object(
        delegate_mod, "invoke_pwsh",
        return_value=(1, "", "Add-MailboxPermission : access denied"),
    )

    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = Operation(
        op_id="op-grant-err", action="mail.delegate.grant",
        drive_id="", item_id="",
        args={
            "mailbox": "team@example.com",
            "delegate": "alice@example.com",
            "access_rights": "FullAccess",
        },
        dry_run_result="",
    )

    result = execute_grant(op, logger, before={})

    assert result.status == "error"
    assert result.error is not None
    assert "access denied" in result.error
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-grant-err"]
    assert entries[-1]["result"] == "error"


# ---------------------------------------------------------------------------
# pwsh missing → install hint
# ---------------------------------------------------------------------------

def test_execute_grant_handles_pwsh_missing(tmp_path, mocker):
    mocker.patch.object(
        delegate_mod, "invoke_pwsh",
        side_effect=FileNotFoundError(2, "No such file", "pwsh"),
    )

    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = Operation(
        op_id="op-grant-nopwsh", action="mail.delegate.grant",
        drive_id="", item_id="",
        args={
            "mailbox": "team@example.com",
            "delegate": "alice@example.com",
            "access_rights": "FullAccess",
        },
        dry_run_result="",
    )

    result = execute_grant(op, logger, before={})

    assert result.status == "error"
    assert result.error is not None
    assert "pwsh" in result.error.lower()
    assert "ExchangeOnlineManagement" in result.error
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-grant-nopwsh"]
    assert entries[-1]["result"] == "error"


# ---------------------------------------------------------------------------
# Audit start / end cmd names match the plan
# ---------------------------------------------------------------------------

def test_audit_cmd_names_are_grant_and_revoke(tmp_path, mocker):
    mocker.patch.object(
        delegate_mod, "invoke_pwsh",
        return_value=(0, json.dumps({"status": "ok"}), ""),
    )

    logger = AuditLogger(ops_dir=tmp_path / "ops")
    grant_op = Operation(
        op_id="op-G", action="mail.delegate.grant",
        drive_id="", item_id="",
        args={"mailbox": "m@x", "delegate": "d@x", "access_rights": "FullAccess"},
        dry_run_result="",
    )
    revoke_op = Operation(
        op_id="op-R", action="mail.delegate.revoke",
        drive_id="", item_id="",
        args={"mailbox": "m@x", "delegate": "d@x", "access_rights": "FullAccess"},
        dry_run_result="",
    )
    execute_grant(grant_op, logger, before={})
    execute_revoke(revoke_op, logger, before={})

    starts = {e["op_id"]: e for e in iter_audit_entries(logger)
              if e.get("phase") == "start"}
    assert starts["op-G"]["cmd"] == "mail-delegate-grant"
    assert starts["op-R"]["cmd"] == "mail-delegate-revoke"
