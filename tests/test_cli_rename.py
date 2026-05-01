from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx

from m365ctl.onedrive.cli.rename import run_rename


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
    mocker.patch("m365ctl.onedrive.cli.rename.load_config", return_value=cfg)
    mocker.patch(
        "m365ctl.onedrive.cli.rename._lookup_item",
        return_value={"drive_id": "d1", "item_id": "i1",
                      "full_path": "/x.txt", "name": "x.txt",
                      "parent_path": "/"},
    )
    client = MagicMock()
    mocker.patch("m365ctl.onedrive.cli.rename.build_graph_client", return_value=client)

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


def test_from_plan_renames_in_batch(tmp_path, mocker):
    """Bulk plan execution: phase-0 metadata GETs + phase-2 PATCH batch."""
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.rename.load_config", return_value=cfg)

    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        posts.append(body)
        responses = []
        for r in body["requests"]:
            if r["method"] == "GET":
                item_id = r["url"].split("/items/")[1].split("?")[0]
                responses.append({
                    "id": r["id"], "status": 200, "headers": {},
                    "body": {
                        "id": item_id, "name": item_id,
                        "parentReference": {"id": "P", "path": "/drive/root:/src"},
                    },
                })
            else:
                # PATCH — echo new_name
                request_body = r.get("body") or {}
                responses.append({
                    "id": r["id"], "status": 200, "headers": {},
                    "body": {"id": "ignored", "name": request_body.get("name", "?")},
                })
        return httpx.Response(200, json={"responses": responses})

    from m365ctl.common.graph import GraphClient
    real = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    mocker.patch("m365ctl.onedrive.cli.rename.build_graph_client", return_value=real)

    from m365ctl.common.planfile import PLAN_SCHEMA_VERSION
    plan = {
        "version": PLAN_SCHEMA_VERSION,
        "created_at": "2026-04-24T10:00:00+00:00",
        "source_cmd": "od-rename --from-plan",
        "scope": "drive:d1",
        "operations": [
            {"op_id": f"op-{i}", "action": "rename",
             "drive_id": "d1", "item_id": f"I{i}",
             "args": {"new_name": f"r{i}.txt"},
             "dry_run_result": ""} for i in range(3)
        ],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))

    rc = run_rename(
        config_path=tmp_path / "config.toml",
        scope=None, drive_id=None, item_id=None,
        new_name=None, from_plan=plan_path, plan_out=None,
        confirm=True, unsafe_scope=False,
    )
    assert rc == 0
    assert len(posts) == 2
    assert all(r["method"] == "GET" for r in posts[0]["requests"])
    assert all(r["method"] == "PATCH" for r in posts[1]["requests"])
    assert len(posts[1]["requests"]) == 3
