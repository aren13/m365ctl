from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import httpx

from fazla_od.audit import AuditLogger, iter_audit_entries
from fazla_od.config import CatalogConfig, Config, LoggingConfig, ScopeConfig
from fazla_od.graph import GraphClient
from fazla_od.mutate.clean import (
    purge_recycle_bin_item,
    remove_old_versions,
    revoke_stale_shares,
)
from fazla_od.planfile import Operation


def _client(handler):
    return GraphClient(token_provider=lambda: "t",
                       transport=httpx.MockTransport(handler),
                       sleep=lambda s: None)


def _stub_cfg(tmp_path: Path) -> Config:
    return Config(
        tenant_id="tenant-1", client_id="client-1",
        cert_path=tmp_path / "fazla-od.pfx",
        cert_public=tmp_path / "fazla-od.cer",
        default_auth="app-only",
        scope=ScopeConfig(allow_drives=["d1"], allow_users=["*"],
                          deny_paths=[], unsafe_requires_flag=True),
        catalog=CatalogConfig(path=tmp_path / "catalog.duckdb"),
        logging=LoggingConfig(ops_dir=tmp_path / "logs/ops"),
    )


def test_recycle_bin_purge_is_explicit_command(tmp_path):
    """Only od-clean calls permanentDelete; od-delete never does."""
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(204)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-1", action="recycle-purge",
                   drive_id="d1", item_id="I",
                   args={}, dry_run_result="")
    result = purge_recycle_bin_item(op, _client(handler), logger,
                                    before={"parent_path": "(recycle bin)",
                                            "name": "old.txt"})
    assert result.status == "ok"
    assert result.after["irreversible"] is True
    assert any("permanentDelete" in p for _, p in calls)


def test_remove_old_versions_keeps_n_most_recent(tmp_path):
    now = datetime.now(timezone.utc)
    versions = [
        {"id": f"v{i}", "lastModifiedDateTime":
         (now - timedelta(days=i)).isoformat().replace("+00:00", "Z")}
        for i in range(5)
    ]
    deleted: list[str] = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"value": versions})
        if request.method == "DELETE":
            deleted.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(204)
        return httpx.Response(405)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-2", action="version-delete",
                   drive_id="d1", item_id="i1",
                   args={"keep": 2}, dry_run_result="")
    result = remove_old_versions(op, _client(handler), logger,
                                 before={"parent_path": "/", "name": "x"})
    assert result.status == "ok"
    assert set(deleted) == {"v2", "v3", "v4"}


def test_revoke_stale_shares_only_touches_links_older_than_cutoff(tmp_path):
    now = datetime.now(timezone.utc)
    perms = [
        {"id": "p-fresh",
         "link": {"createdDateTime":
                  (now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                  "scope": "anonymous", "type": "view"}},
        {"id": "p-stale",
         "link": {"createdDateTime":
                  (now - timedelta(days=400)).isoformat().replace("+00:00", "Z"),
                  "scope": "anonymous", "type": "view"}},
        {"id": "p-owner", "roles": ["owner"]},
    ]
    deleted: list[str] = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"value": perms})
        if request.method == "DELETE":
            deleted.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(204)
        return httpx.Response(405)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-3", action="share-revoke",
                   drive_id="d1", item_id="i1",
                   args={"older_than_days": 90}, dry_run_result="")
    result = revoke_stale_shares(op, _client(handler), logger,
                                 before={"parent_path": "/", "name": "x"})
    assert result.status == "ok"
    assert deleted == ["p-stale"]


def test_purge_404_wraps_with_manual_instructions(tmp_path, mocker):
    """Graph /permanentDelete 404s on recycle-bin items AND pwsh is not
    installed; we add a line pointing operators at SharePoint REST / PnP
    for the real purge."""
    def handler(request):
        if request.url.path.endswith("/permanentDelete"):
            return httpx.Response(
                404, json={"error": {"code": "itemNotFound",
                                     "message": "Item not found"}}
            )
        # _lookup_site_url hits GET /drives/{id}
        return httpx.Response(
            200,
            json={"id": "d1",
                  "webUrl": "https://fazla.sharepoint.com/sites/Foo/Shared%20Documents"},
        )

    # Simulate pwsh not on PATH — fallback unavailable, legacy wrap applies.
    mocker.patch("fazla_od.mutate._pwsh.subprocess.run",
                 side_effect=FileNotFoundError("pwsh: not found"))

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-purge", action="recycle-purge",
                   drive_id="d1", item_id="I",
                   args={}, dry_run_result="")
    cfg = _stub_cfg(tmp_path)
    result = purge_recycle_bin_item(op, _client(handler), logger,
                                    before={"parent_path": "(recycle bin)",
                                            "name": "old.txt"},
                                    cfg=cfg)
    assert result.status == "error"
    assert "itemNotFound" in result.error
    assert "Clear-PnPRecycleBinItem" in result.error


def test_purge_falls_back_to_pnp_on_404(tmp_path, mocker):
    """Graph returns itemNotFound; the PnP fallback runs and succeeds."""
    def handler(request):
        if request.url.path.endswith("/permanentDelete"):
            return httpx.Response(
                404, json={"error": {"code": "itemNotFound",
                                     "message": "Item not found"}}
            )
        # _lookup_site_url hits GET /drives/{id}
        return httpx.Response(
            200,
            json={"id": "d1",
                  "webUrl": "https://fazla.sharepoint.com/sites/Foo/Shared%20Documents"},
        )

    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = json.dumps({
        "recycle_bin_item_id": "abc-123",
        "purged_name": "old.txt",
    })
    completed.stderr = ""
    run = mocker.patch("fazla_od.mutate._pwsh.subprocess.run",
                       return_value=completed)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-p1", action="recycle-purge",
                   drive_id="d1", item_id="I",
                   args={}, dry_run_result="")
    cfg = _stub_cfg(tmp_path)
    result = purge_recycle_bin_item(op, _client(handler), logger,
                                    before={"parent_path": "/Shared Documents/_fazla_smoke",
                                            "name": "old.txt"},
                                    cfg=cfg)

    assert result.status == "ok"
    assert result.after["recycle_bin_item_id"] == "abc-123"
    assert result.after["irreversible"] is True
    assert result.after["parent_path"] == "(permanently deleted)"
    # Subprocess called with the PS script + expected params.
    run.assert_called_once()
    argv = run.call_args[0][0]
    assert argv[0] == "pwsh"
    assert any(a.endswith("recycle-purge.ps1") for a in argv)
    assert argv[argv.index("-Tenant") + 1] == "tenant-1"
    assert argv[argv.index("-ClientId") + 1] == "client-1"
    assert argv[argv.index("-SiteUrl") + 1] == "https://fazla.sharepoint.com/sites/Foo"
    assert argv[argv.index("-LeafName") + 1] == "old.txt"
    assert argv[argv.index("-DirName") + 1] == "Shared Documents/_fazla_smoke"
    # Audit-end recorded as ok.
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-p1"]
    assert entries[-1]["result"] == "ok"


def test_purge_via_pnp_normalizes_graph_path_to_site_relative_dir_name(tmp_path, mocker):
    """Symmetric to the restore test: the audit record's full Graph
    ``parent_path`` (``/drives/<id>/root:/F``) must be normalized to the
    site-relative tail before reaching PnP's ``-DirName`` wildcard match."""
    def handler(request):
        if request.url.path.endswith("/permanentDelete"):
            return httpx.Response(
                404, json={"error": {"code": "itemNotFound",
                                     "message": "Item not found"}}
            )
        return httpx.Response(
            200,
            json={"id": "d1",
                  "webUrl": "https://fazla.sharepoint.com/sites/Foo/Shared%20Documents"},
        )

    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = json.dumps({
        "recycle_bin_item_id": "abc-123",
        "purged_name": "old.txt",
    })
    completed.stderr = ""
    run = mocker.patch("fazla_od.mutate._pwsh.subprocess.run",
                       return_value=completed)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-pn", action="recycle-purge",
                   drive_id="d1", item_id="I",
                   args={}, dry_run_result="")
    cfg = _stub_cfg(tmp_path)
    result = purge_recycle_bin_item(op, _client(handler), logger,
                                    before={"parent_path": "/drives/abc/root:/_fazla_smoke2",
                                            "name": "old.txt"},
                                    cfg=cfg)

    assert result.status == "ok"
    run.assert_called_once()
    argv = run.call_args[0][0]
    assert argv[argv.index("-DirName") + 1] == "_fazla_smoke2"
    assert argv[argv.index("-LeafName") + 1] == "old.txt"


def test_purge_falls_through_to_manual_wrap_when_library_suffix_unknown(tmp_path):
    """Graph /permanentDelete returns 404 AND site-URL lookup fails with
    unknownLibrarySuffix; result preserves all three signals (original
    Graph error, lookup error, manual-instructions wrap) without ever
    shelling out to pwsh."""
    def handler(request):
        if request.url.path.endswith("/permanentDelete"):
            return httpx.Response(
                404,
                json={"error": {"code": "itemNotFound",
                                "message": "Item not found"}},
            )
        if request.url.path == "/v1.0/drives/d1":
            return httpx.Response(
                200,
                json={"id": "d1",
                      "webUrl": "https://tenant.sharepoint.com/sites/Foo/SomeCustomLibraryName"},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-plf", action="recycle-purge",
                   drive_id="d1", item_id="I",
                   args={}, dry_run_result="")
    cfg = _stub_cfg(tmp_path)
    # Intentionally do NOT patch subprocess.run — the site-URL lookup
    # raises before we ever try to shell out.
    result = purge_recycle_bin_item(op, _client(handler), logger,
                                    before={"parent_path": "(recycle bin)",
                                            "name": "old.txt"},
                                    cfg=cfg)

    assert result.status == "error"
    # Original Graph error preserved.
    assert "itemNotFound" in result.error
    # Lookup failure surfaced.
    assert "unknownLibrarySuffix" in result.error
    # Purge's manual-instructions wrap landed.
    assert "Clear-PnPRecycleBinItem" in result.error


def test_purge_pnp_failure_propagates_stderr(tmp_path, mocker):
    """Graph returns 404; PS fallback runs but fails — stderr propagates
    into CleanResult.error without the legacy manual-wrap text."""
    def handler(request):
        if request.url.path.endswith("/permanentDelete"):
            return httpx.Response(
                404, json={"error": {"code": "itemNotFound",
                                     "message": "Item not found"}}
            )
        return httpx.Response(
            200,
            json={"id": "d1",
                  "webUrl": "https://fazla.sharepoint.com/sites/Foo/Shared%20Documents"},
        )

    completed = MagicMock()
    completed.returncode = 1
    completed.stdout = ""
    completed.stderr = "Clear-PnPRecycleBinItem: access denied"
    mocker.patch("fazla_od.mutate._pwsh.subprocess.run",
                 return_value=completed)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-p2", action="recycle-purge",
                   drive_id="d1", item_id="I",
                   args={}, dry_run_result="")
    cfg = _stub_cfg(tmp_path)
    result = purge_recycle_bin_item(op, _client(handler), logger,
                                    before={"parent_path": "/Shared Documents/_fazla_smoke",
                                            "name": "old.txt"},
                                    cfg=cfg)

    assert result.status == "error"
    assert "access denied" in result.error
    # Guard against regression that re-wraps PS stderr with the legacy
    # _ODFB_PURGE_MANUAL text; that phrase is unique to the manual wrap.
    assert "Graph v1.0 /permanentDelete" not in result.error
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-p2"]
    assert entries[-1]["result"] == "error"
