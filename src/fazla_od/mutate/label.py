"""Sensitivity-label operations via PnP.PowerShell."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fazla_od.audit import AuditLogger, log_mutation_end, log_mutation_start
from fazla_od.planfile import Operation

_PS1 = Path(__file__).resolve().parents[2].parent / "scripts" / "ps" / "Set-FazlaLabel.ps1"


@dataclass(frozen=True)
class LabelResult:
    op_id: str
    status: str
    error: str | None = None
    after: dict[str, Any] | None = None


def _invoke(ps_args: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(_PS1), *ps_args],
        capture_output=True, text=True, check=False,
    )
    return result.returncode, result.stdout, result.stderr


def execute_label_apply(
    op: Operation, logger: AuditLogger, *, before: dict[str, Any],
) -> LabelResult:
    log_mutation_start(logger, op_id=op.op_id, cmd="od-label(apply)",
                       args=op.args, drive_id=op.drive_id,
                       item_id=op.item_id, before=before)
    code, out, err = _invoke([
        "-Action", "apply",
        "-SiteUrl", op.args["site_url"],
        "-ServerRelativeUrl", before["server_relative_url"],
        "-Label", op.args["label"],
    ])
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
    code, out, err = _invoke([
        "-Action", "remove",
        "-SiteUrl", op.args["site_url"],
        "-ServerRelativeUrl", before["server_relative_url"],
    ])
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
