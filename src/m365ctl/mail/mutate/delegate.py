"""Mailbox delegation via the Set-MailboxDelegate.ps1 PnP.PowerShell wrapper."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.planfile import Operation
from m365ctl.onedrive.mutate._pwsh import PS_SCRIPTS_DIR, invoke_pwsh

_PS_SCRIPT = PS_SCRIPTS_DIR / "Set-MailboxDelegate.ps1"


AccessRights = Literal["FullAccess", "SendAs", "SendOnBehalf"]


@dataclass(frozen=True)
class DelegateEntry:
    kind: str  # 'FullAccess' | 'SendAs' | 'SendOnBehalf'
    mailbox: str
    delegate: str
    access_rights: str
    deny: bool


@dataclass
class DelegateResult:
    op_id: str
    status: str  # 'ok' | 'error'
    error: str | None = None
    after: dict[str, Any] = field(default_factory=dict)


_PWSH_HINT = (
    "pwsh not on PATH. Install PowerShell 7+ and the ExchangeOnlineManagement "
    "module: `Install-Module ExchangeOnlineManagement -Scope CurrentUser`."
)


def list_delegates(mailbox: str) -> list[DelegateEntry]:
    """Run Set-MailboxDelegate.ps1 -Action List and parse JSONL output."""
    try:
        rc, out, err = invoke_pwsh(
            _PS_SCRIPT, ["-Mailbox", mailbox, "-Action", "List"]
        )
    except FileNotFoundError as e:
        raise RuntimeError(_PWSH_HINT) from e
    if rc != 0:
        raise RuntimeError(
            f"List-Delegates failed (rc={rc}): {err.strip() or out.strip()}"
        )
    out_lines = [line for line in out.splitlines() if line.strip()]
    entries: list[DelegateEntry] = []
    for line in out_lines:
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue  # noise / informational lines
        entries.append(
            DelegateEntry(
                kind=d.get("kind", ""),
                mailbox=d.get("mailbox", mailbox),
                delegate=d.get("delegate", ""),
                access_rights=d.get("access_rights", ""),
                deny=bool(d.get("deny", False)),
            )
        )
    return entries


def execute_grant(
    op: Operation,
    logger: AuditLogger,
    *,
    before: dict | None = None,
) -> DelegateResult:
    return _do(op, logger, action="Grant", before=before)


def execute_revoke(
    op: Operation,
    logger: AuditLogger,
    *,
    before: dict | None = None,
) -> DelegateResult:
    return _do(op, logger, action="Revoke", before=before)


def _do(
    op: Operation,
    logger: AuditLogger,
    *,
    action: str,
    before: dict | None,
) -> DelegateResult:
    args = op.args
    mailbox = args["mailbox"]
    delegate = args["delegate"]
    access_rights = args.get("access_rights", "FullAccess")
    log_mutation_start(
        logger,
        op_id=op.op_id,
        cmd=f"mail-delegate-{action.lower()}",
        args=args,
        drive_id=op.drive_id,
        item_id=op.item_id,
        before=before or {},
    )
    try:
        rc, out, err = invoke_pwsh(
            _PS_SCRIPT,
            [
                "-Mailbox", mailbox,
                "-Action", action,
                "-Delegate", delegate,
                "-AccessRights", access_rights,
            ],
        )
    except FileNotFoundError:
        log_mutation_end(
            logger, op_id=op.op_id, after={}, result="error", error=_PWSH_HINT,
        )
        return DelegateResult(op_id=op.op_id, status="error", error=_PWSH_HINT)
    if rc != 0:
        msg = err.strip() or out.strip() or f"pwsh exited with code {rc}"
        log_mutation_end(
            logger, op_id=op.op_id, after={}, result="error", error=msg,
        )
        return DelegateResult(op_id=op.op_id, status="error", error=msg)
    after = {
        "action": action,
        "mailbox": mailbox,
        "delegate": delegate,
        "access_rights": access_rights,
    }
    log_mutation_end(
        logger, op_id=op.op_id, after=after, result="ok",
    )
    return DelegateResult(op_id=op.op_id, status="ok", after=after)
