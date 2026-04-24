from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.cli.label import run_label


def _stub_cfg(tmp_path: Path):
    from m365ctl.config import CatalogConfig, Config, LoggingConfig, ScopeConfig
    return Config(
        tenant_id="t", client_id="c",
        cert_path=tmp_path / "k", cert_public=tmp_path / "c",
        default_auth="app-only",
        scope=ScopeConfig(allow_drives=["d1"], allow_users=["*"],
                          deny_paths=[], unsafe_requires_flag=True),
        catalog=CatalogConfig(path=tmp_path / "catalog.duckdb"),
        logging=LoggingConfig(ops_dir=tmp_path / "logs/ops"),
    )


def test_apply_dry_run_no_subprocess(tmp_path, mocker, capsys):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.cli.label.load_config", return_value=cfg)
    mocker.patch(
        "m365ctl.cli.label._lookup_label_item",
        return_value={"drive_id": "d1", "item_id": "i1",
                      "full_path": "/X/x.docx", "name": "x.docx",
                      "parent_path": "/X",
                      "server_relative_url": "/X/x.docx"},
    )
    client = MagicMock()
    mocker.patch("m365ctl.cli.label.build_graph_client", return_value=client)
    run_mock = mocker.patch("m365ctl.mutate._pwsh.subprocess.run",
                            side_effect=AssertionError("subprocess must NOT run"))

    rc = run_label(
        config_path=tmp_path / "config.toml",
        subcmd="apply",
        scope="drive:d1", drive_id="d1", item_id="i1",
        label="Confidential",
        site_url="https://fazla.sharepoint.com/",
        server_relative_url=None,
        from_plan=None, plan_out=None,
        confirm=False, unsafe_scope=False,
    )
    assert rc == 0
    run_mock.assert_not_called()
    out = capsys.readouterr().out
    assert "DRY-RUN" in out or "would" in out.lower()


def test_apply_requires_label(tmp_path, mocker, capsys):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.cli.label.load_config", return_value=cfg)
    rc = run_label(
        config_path=tmp_path / "config.toml",
        subcmd="apply",
        scope="drive:d1", drive_id="d1", item_id="i1",
        label=None,
        site_url="https://fazla.sharepoint.com/",
        server_relative_url=None,
        from_plan=None, plan_out=None,
        confirm=False, unsafe_scope=False,
    )
    assert rc == 2
    assert "label" in capsys.readouterr().err.lower()


def test_from_plan_invokes_pwsh_once_per_op(tmp_path, mocker):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.cli.label.load_config", return_value=cfg)
    mocker.patch(
        "m365ctl.cli.label._lookup_label_item",
        side_effect=lambda g, d, i: {
            "drive_id": d, "item_id": i,
            "full_path": f"/A/{i}", "name": i,
            "parent_path": "/A",
            "server_relative_url": f"/A/{i}",
        },
    )
    mocker.patch("m365ctl.cli.label.build_graph_client", return_value=MagicMock())

    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = json.dumps({"status": "ok"})
    completed.stderr = ""
    run_mock = mocker.patch("m365ctl.mutate._pwsh.subprocess.run",
                            return_value=completed)

    from m365ctl.planfile import PLAN_SCHEMA_VERSION
    plan = {
        "version": PLAN_SCHEMA_VERSION,
        "created_at": "2026-04-24T10:00:00+00:00",
        "source_cmd": "od-label apply ...",
        "scope": "drive:d1",
        "operations": [
            {"op_id": f"op-{i}", "action": "label-apply",
             "drive_id": "d1", "item_id": f"I{i}",
             "args": {"label": "Internal",
                      "site_url": "https://fazla.sharepoint.com/"},
             "dry_run_result": ""} for i in range(3)
        ],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))

    rc = run_label(
        config_path=tmp_path / "config.toml",
        subcmd="apply",
        scope=None, drive_id=None, item_id=None,
        label=None, site_url=None, server_relative_url=None,
        from_plan=plan_path, plan_out=None,
        confirm=True, unsafe_scope=False,
    )
    assert rc == 0
    assert run_mock.call_count == 3
