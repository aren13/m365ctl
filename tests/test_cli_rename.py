from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.cli.rename import run_rename


def _stub_cfg(tmp_path: Path):
    from m365ctl.common.config import CatalogConfig, Config, LoggingConfig, ScopeConfig
    return Config(
        tenant_id="t", client_id="c",
        cert_path=tmp_path / "k", cert_public=tmp_path / "c",
        default_auth="app-only",
        scope=ScopeConfig(allow_drives=["d1"], allow_users=["*"],
                          deny_paths=[], unsafe_requires_flag=True),
        catalog=CatalogConfig(path=tmp_path / "catalog.duckdb"),
        logging=LoggingConfig(ops_dir=tmp_path / "logs/ops"),
    )


def test_single_rename_dry_run_no_graph_call(tmp_path, mocker, capsys):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.cli.rename.load_config", return_value=cfg)
    mocker.patch(
        "m365ctl.cli.rename._lookup_item",
        return_value={"drive_id": "d1", "item_id": "i1",
                      "full_path": "/x.txt", "name": "x.txt",
                      "parent_path": "/"},
    )
    client = MagicMock()
    mocker.patch("m365ctl.cli.rename.build_graph_client", return_value=client)

    rc = run_rename(
        config_path=tmp_path / "config.toml",
        scope="drive:d1",
        drive_id="d1", item_id="i1",
        new_name="y.txt",
        from_plan=None, plan_out=None,
        confirm=False, unsafe_scope=False,
    )
    assert rc == 0
    client.patch.assert_not_called()
    assert "DRY-RUN" in capsys.readouterr().out
