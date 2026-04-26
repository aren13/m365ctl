from __future__ import annotations

import json
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.onedrive.mutate.label import execute_label_apply, execute_label_remove
from m365ctl.common.planfile import Operation


def _cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.tenant_id = "tenant-x"
    cfg.client_id = "client-x"
    return cfg


def test_apply_label_invokes_pwsh_and_logs(tmp_path, mocker):
    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = json.dumps({"status": "ok", "label": "Confidential"})
    completed.stderr = ""
    run = mocker.patch("m365ctl.onedrive.mutate._pwsh.subprocess.run",
                       return_value=completed)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-1", action="label-apply", drive_id="d1",
                   item_id="i1", args={"label": "Confidential",
                                        "site_url": "https://contoso.sharepoint.com/"},
                   dry_run_result="")
    result = execute_label_apply(op, logger,
                                 before={"parent_path": "/", "name": "x",
                                         "server_relative_url": "/Documents/x"},
                                 cfg=_cfg())
    assert result.status == "ok"
    run.assert_called_once()
    cmd = run.call_args[0][0]
    assert cmd[0] == "pwsh"
    assert any("Set-M365ctlLabel.ps1" in a for a in cmd)
    assert "Confidential" in cmd
    # Tenant + client must be passed through so PnP can Connect-PnPOnline.
    tenant_idx = cmd.index("-Tenant")
    client_idx = cmd.index("-ClientId")
    assert cmd[tenant_idx + 1] == "tenant-x"
    assert cmd[client_idx + 1] == "client-x"
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-1"]
    assert entries[-1]["result"] == "ok"


def test_label_apply_handles_pwsh_missing(tmp_path, mocker):
    mocker.patch(
        "m365ctl.onedrive.mutate._pwsh.subprocess.run",
        side_effect=FileNotFoundError(2, "No such file", "pwsh"),
    )

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-3", action="label-apply", drive_id="d1",
                   item_id="i1", args={"label": "Confidential",
                                        "site_url": "https://contoso.sharepoint.com/"},
                   dry_run_result="")
    result = execute_label_apply(op, logger,
                                 before={"parent_path": "/", "name": "x",
                                         "server_relative_url": "/Documents/x"},
                                 cfg=_cfg())
    assert result.status == "error"
    assert result.error is not None
    assert "pwsh" in result.error.lower()
    assert "PATH" in result.error
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-3"]
    assert entries[-1]["result"] == "error"


def test_remove_label_invokes_pwsh_and_logs_error_on_nonzero(tmp_path, mocker):
    completed = MagicMock()
    completed.returncode = 1
    completed.stdout = ""
    completed.stderr = "Set-PnPFileSensitivityLabel : access denied"
    mocker.patch("m365ctl.onedrive.mutate._pwsh.subprocess.run", return_value=completed)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-2", action="label-remove", drive_id="d1",
                   item_id="i1", args={"site_url":
                                       "https://contoso.sharepoint.com/"},
                   dry_run_result="")
    result = execute_label_remove(op, logger,
                                  before={"parent_path": "/", "name": "x",
                                          "server_relative_url":
                                              "/Documents/x"},
                                  cfg=_cfg())
    assert result.status == "error"
    assert "access denied" in result.error.lower()
