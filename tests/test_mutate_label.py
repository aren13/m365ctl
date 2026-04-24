from __future__ import annotations

import json
from unittest.mock import MagicMock

from fazla_od.audit import AuditLogger, iter_audit_entries
from fazla_od.mutate.label import execute_label_apply, execute_label_remove
from fazla_od.planfile import Operation


def test_apply_label_invokes_pwsh_and_logs(tmp_path, mocker):
    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = json.dumps({"status": "ok", "label": "Confidential"})
    completed.stderr = ""
    run = mocker.patch("fazla_od.mutate._pwsh.subprocess.run",
                       return_value=completed)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-1", action="label-apply", drive_id="d1",
                   item_id="i1", args={"label": "Confidential",
                                        "site_url": "https://fazla.sharepoint.com/"},
                   dry_run_result="")
    result = execute_label_apply(op, logger,
                                 before={"parent_path": "/", "name": "x",
                                         "server_relative_url": "/Documents/x"})
    assert result.status == "ok"
    run.assert_called_once()
    cmd = run.call_args[0][0]
    assert cmd[0] == "pwsh"
    assert any("Set-FazlaLabel.ps1" in a for a in cmd)
    assert "Confidential" in cmd
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-1"]
    assert entries[-1]["result"] == "ok"


def test_remove_label_invokes_pwsh_and_logs_error_on_nonzero(tmp_path, mocker):
    completed = MagicMock()
    completed.returncode = 1
    completed.stdout = ""
    completed.stderr = "Set-PnPFileSensitivityLabel : access denied"
    mocker.patch("fazla_od.mutate._pwsh.subprocess.run", return_value=completed)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-2", action="label-remove", drive_id="d1",
                   item_id="i1", args={"site_url":
                                       "https://fazla.sharepoint.com/"},
                   dry_run_result="")
    result = execute_label_remove(op, logger,
                                  before={"parent_path": "/", "name": "x",
                                          "server_relative_url":
                                              "/Documents/x"})
    assert result.status == "error"
    assert "access denied" in result.error.lower()
