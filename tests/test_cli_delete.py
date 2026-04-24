from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx

from m365ctl.onedrive.cli.delete import run_delete


def _stub_cfg(tmp_path: Path, *, allow=None, deny=None):
    from m365ctl.common.config import CatalogConfig, Config, LoggingConfig, ScopeConfig
    return Config(
        tenant_id="t", client_id="c",
        cert_path=tmp_path / "k", cert_public=tmp_path / "c",
        default_auth="app-only",
        scope=ScopeConfig(
            allow_drives=allow or ["d1"],
            allow_users=["*"],
            deny_paths=deny or [],
            unsafe_requires_flag=True,
        ),
        catalog=CatalogConfig(path=tmp_path / "catalog.duckdb"),
        logging=LoggingConfig(ops_dir=tmp_path / "logs/ops"),
    )


def test_dry_run_is_default_no_graph_call(tmp_path, mocker, capsys):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.delete.load_config", return_value=cfg)
    client = MagicMock()
    mocker.patch("m365ctl.onedrive.cli.delete.build_graph_client", return_value=client)
    mocker.patch(
        "m365ctl.onedrive.cli.delete._lookup_item",
        return_value={"drive_id": "d1", "item_id": "i1",
                      "full_path": "/A/x.tmp", "name": "x.tmp",
                      "parent_path": "/A"},
    )

    rc = run_delete(
        config_path=tmp_path / "config.toml",
        scope="drive:d1", drive_id="d1", item_id="i1",
        pattern=None, from_plan=None, plan_out=None,
        confirm=False, unsafe_scope=False,
    )
    assert rc == 0
    client.delete.assert_not_called()
    out = capsys.readouterr().out
    assert "DRY-RUN" in out or "would" in out.lower()


def test_confirm_required_for_single_delete_executes(tmp_path, mocker):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.delete.load_config", return_value=cfg)

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.method == "DELETE"
        return httpx.Response(204)

    from m365ctl.common.graph import GraphClient
    real = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    mocker.patch("m365ctl.onedrive.cli.delete.build_graph_client", return_value=real)
    mocker.patch(
        "m365ctl.onedrive.cli.delete._lookup_item",
        return_value={"drive_id": "d1", "item_id": "i1",
                      "full_path": "/A/x", "name": "x",
                      "parent_path": "/A"},
    )

    rc = run_delete(
        config_path=tmp_path / "config.toml",
        scope="drive:d1", drive_id="d1", item_id="i1",
        pattern=None, from_plan=None, plan_out=None,
        confirm=True, unsafe_scope=False,
    )
    assert rc == 0
    assert calls["n"] == 1


def test_pattern_with_confirm_requires_from_plan(tmp_path, mocker, capsys):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.delete.load_config", return_value=cfg)
    rc = run_delete(
        config_path=tmp_path / "config.toml",
        scope="drive:d1", drive_id=None, item_id=None,
        pattern="**/*.tmp", from_plan=None, plan_out=None,
        confirm=True, unsafe_scope=False,
    )
    assert rc == 2
    assert "plan" in capsys.readouterr().err.lower()


def test_deny_paths_filtered_from_plan(tmp_path, mocker):
    """Deny-paths never appear in an emitted plan."""
    cfg = _stub_cfg(tmp_path, deny=["/Confidential/**"])
    mocker.patch("m365ctl.onedrive.cli.delete.load_config", return_value=cfg)

    # Seed a catalog with one allowed and one denied item.
    from m365ctl.onedrive.catalog.db import open_catalog
    with open_catalog(cfg.catalog.path) as conn:
        conn.execute(
            "INSERT INTO items (drive_id, item_id, name, full_path, "
            "is_folder, is_deleted, parent_path) VALUES "
            "('d1','i1','ok.tmp','/Public/ok.tmp',false,false,'/Public'),"
            "('d1','i2','secret.tmp','/Confidential/secret.tmp',false,false,'/Confidential')"
        )

    plan_out = tmp_path / "plan.json"
    rc = run_delete(
        config_path=tmp_path / "config.toml",
        scope="drive:d1", drive_id=None, item_id=None,
        pattern="**/*.tmp", from_plan=None, plan_out=plan_out,
        confirm=False, unsafe_scope=False,
    )
    assert rc == 0
    assert plan_out.exists()
    plan = json.loads(plan_out.read_text())
    item_ids = [op["item_id"] for op in plan["operations"]]
    assert "i1" in item_ids
    assert "i2" not in item_ids  # filtered by deny-path
