from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx

from fazla_od.cli.clean import run_clean


def _stub_cfg(tmp_path: Path, *, allow=None, deny=None):
    from fazla_od.config import CatalogConfig, Config, LoggingConfig, ScopeConfig
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


def test_recycle_bin_dry_run_emits_plan_of_recycled_items(tmp_path, mocker):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("fazla_od.cli.clean.load_config", return_value=cfg)

    from fazla_od.catalog.db import open_catalog
    with open_catalog(cfg.catalog.path) as conn:
        conn.execute(
            "INSERT INTO items (drive_id, item_id, name, full_path, "
            "is_folder, is_deleted, parent_path) VALUES "
            "('d1','i1','a.tmp','/A/a.tmp',false,false,'/A'),"
            "('d1','i2','b.tmp','/A/b.tmp',false,false,'/A')"
        )

    plan_out = tmp_path / "plan.json"
    rc = run_clean(
        config_path=tmp_path / "config.toml",
        subcmd="recycle-bin",
        scope="drive:d1", drive_id=None, item_id=None,
        pattern="**/*.tmp", from_plan=None, plan_out=plan_out,
        keep=None, older_than_days=None,
        confirm=False, unsafe_scope=False,
    )
    assert rc == 0
    plan = json.loads(plan_out.read_text())
    actions = [op["action"] for op in plan["operations"]]
    assert actions == ["recycle-purge", "recycle-purge"]


def test_old_versions_plan_one_op_per_item(tmp_path, mocker):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("fazla_od.cli.clean.load_config", return_value=cfg)

    from fazla_od.catalog.db import open_catalog
    with open_catalog(cfg.catalog.path) as conn:
        conn.execute(
            "INSERT INTO items (drive_id, item_id, name, full_path, "
            "is_folder, is_deleted, parent_path) VALUES "
            "('d1','i1','x','/X',false,false,'/')"
        )

    plan_out = tmp_path / "plan.json"
    rc = run_clean(
        config_path=tmp_path / "config.toml",
        subcmd="old-versions",
        scope="drive:d1", drive_id=None, item_id=None,
        pattern="/X", from_plan=None, plan_out=plan_out,
        keep=5, older_than_days=None,
        confirm=False, unsafe_scope=False,
    )
    assert rc == 0
    plan = json.loads(plan_out.read_text())
    assert len(plan["operations"]) == 1
    assert plan["operations"][0]["action"] == "version-delete"
    assert plan["operations"][0]["args"]["keep"] == 5


def test_stale_shares_older_than_days_honored(tmp_path, mocker):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("fazla_od.cli.clean.load_config", return_value=cfg)

    from fazla_od.catalog.db import open_catalog
    with open_catalog(cfg.catalog.path) as conn:
        conn.execute(
            "INSERT INTO items (drive_id, item_id, name, full_path, "
            "is_folder, is_deleted, parent_path) VALUES "
            "('d1','i1','x','/X',false,false,'/')"
        )

    plan_out = tmp_path / "plan.json"
    rc = run_clean(
        config_path=tmp_path / "config.toml",
        subcmd="stale-shares",
        scope="drive:d1", drive_id=None, item_id=None,
        pattern="/X", from_plan=None, plan_out=plan_out,
        keep=None, older_than_days=30,
        confirm=False, unsafe_scope=False,
    )
    assert rc == 0
    plan = json.loads(plan_out.read_text())
    assert plan["operations"][0]["args"]["older_than_days"] == 30


def test_confirm_required_to_execute_bulk_pattern(tmp_path, mocker, capsys):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("fazla_od.cli.clean.load_config", return_value=cfg)
    rc = run_clean(
        config_path=tmp_path / "config.toml",
        subcmd="recycle-bin",
        scope="drive:d1", drive_id=None, item_id=None,
        pattern="**/*.tmp", from_plan=None, plan_out=None,
        keep=None, older_than_days=None,
        confirm=True, unsafe_scope=False,
    )
    assert rc == 2
    assert "plan" in capsys.readouterr().err.lower()
