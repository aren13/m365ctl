from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx

from fazla_od.audit import AuditLogger, log_mutation_end, log_mutation_start
from fazla_od.cli.clean import run_clean
from fazla_od.graph import GraphError
from fazla_od.mutate.clean import CleanResult


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


def _write_purge_plan(path: Path, drive_id: str, item_id: str, op_id: str) -> None:
    path.write_text(json.dumps({
        "version": 1,
        "source_cmd": "smoke",
        "scope": "me",
        "operations": [
            {"op_id": op_id, "action": "recycle-purge",
             "drive_id": drive_id, "item_id": item_id,
             "args": {}, "dry_run_result": "smoke"}
        ],
    }))


def test_purge_via_plan_recovers_before_from_prior_delete_audit_record(
    tmp_path, mocker,
):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("fazla_od.cli.clean.load_config", return_value=cfg)

    # Seed a prior od-delete audit record for (d1, i1).
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    log_mutation_start(
        logger, op_id="del-1", cmd="od-delete", args={},
        drive_id="d1", item_id="i1",
        before={"parent_path": "/F", "name": "hello.txt"},
    )
    log_mutation_end(
        logger, op_id="del-1",
        after={"parent_path": "(recycle bin)", "name": "hello.txt",
               "recycled_from": "/F"},
        result="ok",
    )

    # Build the purge plan for that same (d1, i1).
    plan_path = tmp_path / "purge.json"
    _write_purge_plan(plan_path, "d1", "i1", "smoke-purge-abc")

    # Mock the graph client and _lookup_item so the recycle-bin 404 fires.
    mocker.patch("fazla_od.cli.clean.build_graph_client",
                 return_value=MagicMock())
    mocker.patch(
        "fazla_od.cli.clean._lookup_item",
        side_effect=GraphError("HTTP404: itemNotFound"),
    )
    captured: dict = {}

    def _fake_purge(op, graph, logger, *, before, cfg):  # noqa: ARG001
        captured["before"] = before
        return CleanResult(
            op_id=op.op_id, status="ok",
            after={"parent_path": "(permanently deleted)",
                   "name": before["name"], "irreversible": True},
        )

    mocker.patch("fazla_od.cli.clean.purge_recycle_bin_item",
                 side_effect=_fake_purge)
    # Rebind the dispatch table to the patched callable.
    mocker.patch.dict(
        "fazla_od.cli.clean._ACTION_EXECUTORS",
        {"recycle-purge": _fake_purge},
    )

    rc = run_clean(
        config_path=tmp_path / "config.toml",
        subcmd="recycle-bin",
        scope="me", drive_id=None, item_id=None,
        pattern=None, from_plan=plan_path, plan_out=None,
        keep=None, older_than_days=None,
        confirm=True, unsafe_scope=False,
    )
    assert rc == 0
    assert captured["before"]["name"] == "hello.txt"
    assert captured["before"]["parent_path"] == "/F"


def test_purge_without_prior_delete_audit_warns_operator(
    tmp_path, mocker, capsys,
):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("fazla_od.cli.clean.load_config", return_value=cfg)

    # No prior delete op logged.
    plan_path = tmp_path / "purge.json"
    _write_purge_plan(plan_path, "d1", "orphan", "smoke-purge-orphan")

    mocker.patch("fazla_od.cli.clean.build_graph_client",
                 return_value=MagicMock())
    mocker.patch(
        "fazla_od.cli.clean._lookup_item",
        side_effect=GraphError("HTTP404: itemNotFound"),
    )

    def _fake_purge(op, graph, logger, *, before, cfg):  # noqa: ARG001
        return CleanResult(
            op_id=op.op_id, status="ok",
            after={"parent_path": "(permanently deleted)",
                   "name": before["name"], "irreversible": True},
        )

    mocker.patch.dict(
        "fazla_od.cli.clean._ACTION_EXECUTORS",
        {"recycle-purge": _fake_purge},
    )

    rc = run_clean(
        config_path=tmp_path / "config.toml",
        subcmd="recycle-bin",
        scope="me", drive_id=None, item_id=None,
        pattern=None, from_plan=plan_path, plan_out=None,
        keep=None, older_than_days=None,
        confirm=True, unsafe_scope=False,
    )
    # Purge still returns ok (mocked), but the warning must be on stderr.
    assert rc == 0
    err = capsys.readouterr().err
    assert "no prior od-delete audit record found" in err
    assert "d1" in err
    assert "orphan" in err


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
