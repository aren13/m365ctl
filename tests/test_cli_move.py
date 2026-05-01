from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx

from m365ctl.onedrive.cli.move import run_move


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


def test_single_item_dry_run_does_not_call_graph(tmp_path, mocker, capsys):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.move.load_config", return_value=cfg)
    mock_client = MagicMock()
    mocker.patch("m365ctl.onedrive.cli.move.build_graph_client", return_value=mock_client)
    mocker.patch(
        "m365ctl.onedrive.cli.move._lookup_item",
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
    mocker.patch("m365ctl.onedrive.cli.move.load_config", return_value=cfg)
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
    """Bulk plan execution: phase-0 metadata GET batch + phase-2 PATCH batch."""
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.move.load_config", return_value=cfg)

    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        posts.append(body)
        responses = []
        for r in body["requests"]:
            if r["method"] == "GET":
                # Metadata GET response.
                # URL example: drives/d1/items/I0?$select=...
                item_id = r["url"].split("/items/")[1].split("?")[0]
                responses.append({
                    "id": r["id"], "status": 200, "headers": {},
                    "body": {
                        "id": item_id, "name": item_id,
                        "parentReference": {"id": "OLD-P", "path": "/drive/root:/src"},
                    },
                })
            else:
                responses.append({
                    "id": r["id"], "status": 200, "headers": {},
                    "body": {
                        "id": "ignored", "name": "x",
                        "parentReference": {"id": "P", "path": "/drive/root:/B"},
                    },
                })
        return httpx.Response(200, json={"responses": responses})

    from m365ctl.common.graph import GraphClient
    real_client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    mocker.patch("m365ctl.onedrive.cli.move.build_graph_client", return_value=real_client)

    from m365ctl.common.planfile import PLAN_SCHEMA_VERSION
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
    # Exactly two /$batch POSTs: phase-0 metadata GETs + phase-2 PATCH mutations.
    assert len(posts) == 2
    assert all(r["method"] == "GET" for r in posts[0]["requests"])
    assert all(r["method"] == "PATCH" for r in posts[1]["requests"])
    assert len(posts[1]["requests"]) == 3
