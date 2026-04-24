from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from m365ctl.cli.audit_sharing import run_audit


def _cfg(tmp_path: Path):
    cfg = MagicMock()
    cfg.tenant_id = "tenant-x"
    cfg.client_id = "client-x"
    cfg.cert_path = tmp_path / "k"
    cfg.cert_public = tmp_path / "c"
    cfg.catalog.path = tmp_path / "c.duckdb"
    return cfg


def test_audit_shells_out_and_parses_json(tmp_path, mocker, capsys) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("m365ctl.cli.audit_sharing.load_config", return_value=cfg)

    payload = [
        {"drive_id": "d", "item_id": "i", "full_path": "/A/a.pdf",
         "shared_with": "arda@fazla.com", "permission_level": "owner",
         "is_external": False, "expires_at": None},
    ]
    mocker.patch(
        "m365ctl.cli.audit_sharing.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(payload), stderr=""
        ),
    )
    rc = run_audit(
        config_path=tmp_path / "config.toml",
        scope="site:https://fazla.sharepoint.com",
        output_format="json",
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out) == payload


def test_audit_propagates_nonzero_exit(tmp_path, mocker, capsys) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("m365ctl.cli.audit_sharing.load_config", return_value=cfg)
    mocker.patch(
        "m365ctl.cli.audit_sharing.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Connect-PnPOnline: cert load failed"
        ),
    )
    rc = run_audit(
        config_path=tmp_path / "config.toml",
        scope="site:https://fazla.sharepoint.com",
        output_format="json",
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "cert load failed" in err


def test_audit_tsv_is_emitted_verbatim(tmp_path, mocker, capsys) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("m365ctl.cli.audit_sharing.load_config", return_value=cfg)
    tsv = (
        "drive_id\titem_id\tfull_path\tshared_with\tpermission_level\t"
        "is_external\texpires_at\n"
        "d\ti\t/A/a.pdf\tarda@fazla.com\towner\tFalse\t\n"
    )
    mocker.patch(
        "m365ctl.cli.audit_sharing.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=tsv, stderr=""
        ),
    )
    rc = run_audit(
        config_path=tmp_path / "config.toml",
        scope="site:https://fazla.sharepoint.com",
        output_format="tsv",
    )
    assert rc == 0
    assert capsys.readouterr().out == tsv
