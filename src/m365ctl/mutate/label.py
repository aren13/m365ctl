"""Sensitivity-label operations via PnP.PowerShell."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from m365ctl.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.mutate._pwsh import PS_SCRIPTS_DIR, invoke_pwsh
from m365ctl.planfile import Operation

_PS1 = PS_SCRIPTS_DIR / "Set-FazlaLabel.ps1"

# Shown when pwsh isn't on PATH so the od-label path mirrors the
# recycle-bin fallback paths in delete.py / clean.py instead of letting a
# FileNotFoundError bubble up as an unhandled exception.
_PWSH_NOT_INSTALLED = (
    "pwsh (PowerShell 7+) is not installed or not on PATH. "
    "Install via 'brew install --cask powershell' (macOS) or per "
    "docs/ops/pnp-powershell-setup.md, then retry."
)


@dataclass(frozen=True)
class LabelResult:
    op_id: str
    status: str
    error: str | None = None
    after: dict[str, Any] | None = None


def execute_label_apply(
    op: Operation, logger: AuditLogger, *, before: dict[str, Any],
) -> LabelResult:
    log_mutation_start(logger, op_id=op.op_id, cmd="od-label(apply)",
                       args=op.args, drive_id=op.drive_id,
                       item_id=op.item_id, before=before)
    try:
        code, out, err = invoke_pwsh(_PS1, [
            "-Action", "apply",
            "-SiteUrl", op.args["site_url"],
            "-ServerRelativeUrl", before["server_relative_url"],
            "-Label", op.args["label"],
        ])
    except FileNotFoundError:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=_PWSH_NOT_INSTALLED)
        return LabelResult(op_id=op.op_id, status="error",
                           error=_PWSH_NOT_INSTALLED)
    if code != 0:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=err.strip() or out.strip())
        return LabelResult(op_id=op.op_id, status="error",
                           error=err.strip() or out.strip())
    after = {"parent_path": before.get("parent_path", ""),
             "name": before.get("name", ""),
             "label": op.args["label"]}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return LabelResult(op_id=op.op_id, status="ok", after=after)


def execute_label_remove(
    op: Operation, logger: AuditLogger, *, before: dict[str, Any],
) -> LabelResult:
    log_mutation_start(logger, op_id=op.op_id, cmd="od-label(remove)",
                       args=op.args, drive_id=op.drive_id,
                       item_id=op.item_id, before=before)
    try:
        code, out, err = invoke_pwsh(_PS1, [
            "-Action", "remove",
            "-SiteUrl", op.args["site_url"],
            "-ServerRelativeUrl", before["server_relative_url"],
        ])
    except FileNotFoundError:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=_PWSH_NOT_INSTALLED)
        return LabelResult(op_id=op.op_id, status="error",
                           error=_PWSH_NOT_INSTALLED)
    if code != 0:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=err.strip() or out.strip())
        return LabelResult(op_id=op.op_id, status="error",
                           error=err.strip() or out.strip())
    after = {"parent_path": before.get("parent_path", ""),
             "name": before.get("name", ""),
             "label": None}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return LabelResult(op_id=op.op_id, status="ok", after=after)
