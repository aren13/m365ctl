from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx

from m365ctl.onedrive.cli.copy import run_copy


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
    mocker.patch("m365ctl.onedrive.cli.copy.load_config", return_value=cfg)
    mock_client = MagicMock()
    mocker.patch("m365ctl.onedrive.cli.copy.build_graph_client", return_value=mock_client)
    mocker.patch(
        "m365ctl.onedrive.cli.copy._lookup_item",
        return_value={"drive_id": "d1", "item_id": "i1",
                      "full_path": "/A/x", "name": "x",
                      "parent_path": "/A"},
    )

    rc = run_copy(
        config_path=tmp_path / "config.toml",
        scope="drive:d1",
        drive_id="d1", item_id="i1",
        pattern=None, from_plan=None,
        target_drive_id="d2", target_parent_item_id="PID-B",
        new_name="copy-of-x",
        plan_out=None, confirm=False, unsafe_scope=False,
    )
    assert rc == 0
    mock_client.post_raw.assert_not_called()
    out = capsys.readouterr().out
    assert "DRY-RUN" in out or "would" in out.lower()


def test_pattern_plus_confirm_rejected_without_from_plan(tmp_path, mocker, capsys):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.copy.load_config", return_value=cfg)
    rc = run_copy(
        config_path=tmp_path / "config.toml",
        scope="drive:d1",
        drive_id=None, item_id=None,
        pattern="**/*.pdf", from_plan=None,
        target_drive_id="d2", target_parent_item_id="PID",
        new_name=None,
        plan_out=None, confirm=True, unsafe_scope=False,
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "plan" in err.lower()


def test_from_plan_issues_exactly_one_copy_per_op(tmp_path, mocker):
    """Bulk plan execution: phase-0 metadata GETs + phase-2 copy POSTs.

    The phase-2 batch returns 202 + Location for each sub-response so that
    ``finish_copy`` exercises the monitor-URL capture path.
    """
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.copy.load_config", return_value=cfg)

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
                        "parentReference": {"id": "OLD-P", "path": "/drive/root:/src"},
                    },
                })
            else:
                responses.append({
                    "id": r["id"], "status": 202,
                    "headers": {"Location": f"https://graph/monitor/{r['id']}"},
                    "body": {},
                })
        return httpx.Response(200, json={"responses": responses})

    from m365ctl.common.graph import GraphClient
    real_client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    mocker.patch("m365ctl.onedrive.cli.copy.build_graph_client", return_value=real_client)

    from m365ctl.common.planfile import PLAN_SCHEMA_VERSION
    plan = {
        "version": PLAN_SCHEMA_VERSION,
        "created_at": "2026-04-24T10:00:00+00:00",
        "source_cmd": "od-copy --pattern ...",
        "scope": "drive:d1",
        "operations": [
            {"op_id": f"op-{i}", "action": "copy",
             "drive_id": "d1", "item_id": f"I{i}",
             "args": {"target_drive_id": "d2", "target_parent_item_id": "P",
                      "new_name": f"c{i}"},
             "dry_run_result": ""} for i in range(3)
        ],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))

    rc = run_copy(
        config_path=tmp_path / "config.toml",
        scope=None, drive_id=None, item_id=None, pattern=None,
        from_plan=plan_path,
        target_drive_id=None, target_parent_item_id=None, new_name=None,
        plan_out=None, confirm=True, unsafe_scope=False,
    )
    assert rc == 0
    # Two /$batch POSTs: phase-0 metadata + phase-2 copy.
    assert len(posts) == 2
    assert all(r["method"] == "GET" for r in posts[0]["requests"])
    assert all(r["method"] == "POST" for r in posts[1]["requests"])
    assert len(posts[1]["requests"]) == 3

    # finish_copy must capture the Location header in after.monitor_url.
    from m365ctl.common.audit import AuditLogger, iter_audit_entries
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    ends = [e for e in iter_audit_entries(logger) if e["phase"] == "end"]
    assert len(ends) == 3
    for e in ends:
        assert e["after"]["monitor_url"].startswith("https://graph/monitor/")
