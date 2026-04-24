from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from m365ctl.cli.move import run_move


def _stub_cfg(tmp_path: Path, *, allow=None, deny=None):
    from m365ctl.config import CatalogConfig, Config, LoggingConfig, ScopeConfig
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


def test_single_item_dry_run_does_not_call_graph(tmp_path, mocker, capsys):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.cli.move.load_config", return_value=cfg)
    mock_client = MagicMock()
    mocker.patch("m365ctl.cli.move.build_graph_client", return_value=mock_client)
    mocker.patch(
        "m365ctl.cli.move._lookup_item",
        return_value={"drive_id": "d1", "item_id": "i1",
                      "full_path": "/A/x", "name": "x",
                      "parent_path": "/A"},
    )

    rc = run_move(
        config_path=tmp_path / "config.toml",
        scope="drive:d1",
        item_id="i1",
        drive_id="d1",
        pattern=None,
        from_plan=None,
        new_parent_path="/B",
        new_parent_item_id="PID-B",
        plan_out=None,
        confirm=False,
        unsafe_scope=False,
    )
    assert rc == 0
    mock_client.patch.assert_not_called()
    out = capsys.readouterr().out
    assert "DRY-RUN" in out or "would move" in out.lower()


def test_pattern_plus_confirm_rejected_without_from_plan(tmp_path, mocker, capsys):
    """Spec §7 rule 2: bulk destructive requires a plan file."""
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.cli.move.load_config", return_value=cfg)
    rc = run_move(
        config_path=tmp_path / "config.toml",
        scope="drive:d1", item_id=None, drive_id=None,
        pattern="**/*.tmp",
        from_plan=None,
        new_parent_path="/Trash", new_parent_item_id="TRASH",
        plan_out=None, confirm=True, unsafe_scope=False,
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "plan" in err.lower()


def test_from_plan_issues_exactly_one_patch_per_op(tmp_path, mocker):
    """Counting mock transport — proves no glob re-expansion."""
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.cli.move.load_config", return_value=cfg)

    calls = {"n": 0}
    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200, json={"id": "ignored",
                       "parentReference": {"id": "P", "path": "/B"},
                       "name": "x"})

    from m365ctl.graph import GraphClient
    real_client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    mocker.patch("m365ctl.cli.move.build_graph_client", return_value=real_client)
    mocker.patch(
        "m365ctl.cli.move._lookup_item",
        side_effect=lambda graph, drive_id, item_id: {
            "drive_id": drive_id, "item_id": item_id,
            "full_path": f"/src/{item_id}", "name": item_id,
            "parent_path": "/src",
        },
    )

    from m365ctl.planfile import PLAN_SCHEMA_VERSION
    plan = {
        "version": PLAN_SCHEMA_VERSION,
        "created_at": "2026-04-24T10:00:00+00:00",
        "source_cmd": "od-move --pattern ...",
        "scope": "drive:d1",
        "operations": [
            {"op_id": f"op-{i}", "action": "move",
             "drive_id": "d1", "item_id": f"I{i}",
             "args": {"new_parent_item_id": "P"},
             "dry_run_result": ""} for i in range(3)
        ],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))

    rc = run_move(
        config_path=tmp_path / "config.toml",
        scope=None, item_id=None, drive_id=None, pattern=None,
        from_plan=plan_path,
        new_parent_path=None, new_parent_item_id=None,
        plan_out=None, confirm=True, unsafe_scope=False,
    )
    assert rc == 0
    assert calls["n"] == 3
