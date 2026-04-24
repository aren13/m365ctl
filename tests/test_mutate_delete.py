from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx

from fazla_od.audit import AuditLogger, iter_audit_entries
from fazla_od.config import CatalogConfig, Config, LoggingConfig, ScopeConfig
from fazla_od.graph import GraphClient
from fazla_od.mutate.delete import execute_recycle_delete, execute_restore
from fazla_od.planfile import Operation


def _client(handler):
    return GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )


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


def test_delete_routes_to_recycle_not_permadelete(tmp_path):
    seen: list[tuple[str, str]] = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(204)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-1", action="delete", drive_id="d1", item_id="i1",
                   args={}, dry_run_result="")
    result = execute_recycle_delete(op, _client(handler), logger,
                                    before={"parent_path": "/", "name": "x.txt"})
    assert result.status == "ok"
    # Spec §7 rule 6: no /permanentDelete path.
    assert seen == [("DELETE", "/v1.0/drives/d1/items/i1")]


def test_restore_calls_restore_endpoint(tmp_path):
    seen: list[tuple[str, str]] = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={"id": "i1", "name": "x.txt",
                  "parentReference": {"id": "P", "path": "/drive/root:/A"}},
        )

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-2", action="restore", drive_id="d1", item_id="i1",
                   args={}, dry_run_result="")
    result = execute_restore(op, _client(handler), logger,
                             before={"parent_path": "(recycle bin)", "name": "x.txt"})
    assert result.status == "ok"
    assert seen == [("POST", "/v1.0/drives/d1/items/i1/restore")]


def test_restore_notsupported_wraps_with_manual_instructions(tmp_path, mocker):
    """OneDrive-for-Business restore fails with notSupported AND pwsh is
    not installed; we add a line pointing operators at the SharePoint/PnP
    workaround.
    """
    def handler(request):
        if request.url.path.endswith("/restore"):
            return httpx.Response(
                400,
                json={"error": {"code": "notSupported",
                                "message": "Operation not supported"}},
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
    op = Operation(op_id="op-restore", action="restore", drive_id="d1",
                   item_id="i1", args={}, dry_run_result="")
    cfg = _stub_cfg(tmp_path)
    result = execute_restore(op, _client(handler), logger,
                             before={"parent_path": "(recycle bin)",
                                     "name": "x.txt"},
                             cfg=cfg)
    assert result.status == "error"
    assert "notSupported" in result.error
    assert "PnP.PowerShell" in result.error
    assert "Restore-PnPRecycleBinItem" in result.error


def test_restore_falls_back_to_pnp_on_notsupported(tmp_path, mocker):
    """Graph returns notSupported; the PnP fallback runs and succeeds."""
    def handler(request):
        if request.url.path.endswith("/restore"):
            return httpx.Response(
                400,
                json={"error": {"code": "notSupported",
                                "message": "ODfB not supported"}},
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
        "restored_name": "hello.txt",
        "restored_parent_path": "/Shared Documents/_fazla_smoke",
    })
    completed.stderr = ""
    run = mocker.patch("fazla_od.mutate._pwsh.subprocess.run",
                       return_value=completed)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-r1", action="restore", drive_id="d1",
                   item_id="i1", args={}, dry_run_result="")
    cfg = _stub_cfg(tmp_path)
    result = execute_restore(op, _client(handler), logger,
                             before={"parent_path": "/Shared Documents/_fazla_smoke",
                                     "name": "hello.txt"},
                             cfg=cfg)

    assert result.status == "ok"
    assert result.after["recycle_bin_item_id"] == "abc-123"
    assert result.after["name"] == "hello.txt"
    # Subprocess was called with the PS script + expected params.
    run.assert_called_once()
    argv = run.call_args[0][0]
    assert argv[0] == "pwsh"
    assert any(a.endswith("recycle-restore.ps1") for a in argv)
    assert argv[argv.index("-Tenant") + 1] == "tenant-1"
    assert argv[argv.index("-ClientId") + 1] == "client-1"
    assert "-SiteUrl" in argv
    site_idx = argv.index("-SiteUrl") + 1
    assert argv[site_idx] == "https://fazla.sharepoint.com/sites/Foo"
    assert "-LeafName" in argv
    assert argv[argv.index("-LeafName") + 1] == "hello.txt"
    # Audit-end recorded as ok.
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-r1"]
    assert entries[-1]["result"] == "ok"


def test_restore_via_pnp_normalizes_graph_path_to_site_relative_dir_name(tmp_path, mocker):
    """Audit-logged `before.parent_path` is the full Graph path
    (``/drives/<id>/root:/_fazla_smoke2``). PnP's
    ``Find-RecycleBinItem -DirName`` wildcard match expects the
    site-relative tail — we must strip the ``root:`` prefix before
    invoking the PS script, or PnP reports ``NoMatch``."""
    def handler(request):
        if request.url.path.endswith("/restore"):
            return httpx.Response(
                400,
                json={"error": {"code": "notSupported",
                                "message": "ODfB not supported"}},
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
        "restored_name": "hello2.txt",
        "restored_parent_path": "_fazla_smoke2",
    })
    completed.stderr = ""
    run = mocker.patch("fazla_od.mutate._pwsh.subprocess.run",
                       return_value=completed)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-rn", action="restore", drive_id="d1",
                   item_id="i1", args={}, dry_run_result="")
    cfg = _stub_cfg(tmp_path)
    result = execute_restore(op, _client(handler), logger,
                             before={"parent_path": "/drives/abc/root:/_fazla_smoke2",
                                     "name": "hello2.txt"},
                             cfg=cfg)

    assert result.status == "ok"
    run.assert_called_once()
    argv = run.call_args[0][0]
    # The full Graph path never reaches PS; only the site-relative tail does.
    assert argv[argv.index("-DirName") + 1] == "_fazla_smoke2"
    assert argv[argv.index("-LeafName") + 1] == "hello2.txt"


def test_restore_falls_through_to_manual_wrap_when_library_suffix_unknown(tmp_path):
    """Graph /restore returns ODfB token AND site-URL lookup fails with
    unknownLibrarySuffix; result preserves all three signals (original
    Graph error, lookup error, manual-instructions wrap) without ever
    shelling out to pwsh."""
    def handler(request):
        if request.url.path.endswith("/restore"):
            return httpx.Response(
                400,
                json={"error": {"code": "notSupported",
                                "message": "ODfB not supported"}},
            )
        if request.url.path == "/v1.0/drives/d1":
            return httpx.Response(
                200,
                json={"id": "d1",
                      "webUrl": "https://tenant.sharepoint.com/sites/Foo/SomeCustomLibraryName"},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-rlf", action="restore", drive_id="d1",
                   item_id="i1", args={}, dry_run_result="")
    cfg = _stub_cfg(tmp_path)
    # Intentionally do NOT patch subprocess.run — the site-URL lookup
    # raises before we ever try to shell out. If pwsh were reached the
    # real binary (or its absence) would surface here.
    result = execute_restore(op, _client(handler), logger,
                             before={"parent_path": "/SomeCustomLibraryName/x",
                                     "name": "x.txt"},
                             cfg=cfg)

    assert result.status == "error"
    # Original Graph error preserved.
    assert "notSupported" in result.error
    # Lookup failure surfaced.
    assert "unknownLibrarySuffix" in result.error
    # Manual-instructions wrap landed — operator can still take manual action.
    assert "Restore-PnPRecycleBinItem" in result.error


def test_restore_pnp_failure_propagates_stderr(tmp_path, mocker):
    """Graph returns notSupported; PS fallback runs but fails — stderr
    propagates into DeleteResult.error."""
    def handler(request):
        if request.url.path.endswith("/restore"):
            return httpx.Response(
                400,
                json={"error": {"code": "notSupported",
                                "message": "ODfB not supported"}},
            )
        return httpx.Response(
            200,
            json={"id": "d1",
                  "webUrl": "https://fazla.sharepoint.com/sites/Foo/Shared%20Documents"},
        )

    completed = MagicMock()
    completed.returncode = 1
    completed.stdout = ""
    completed.stderr = "Set-PnPRecycleBinItem: no match"
    mocker.patch("fazla_od.mutate._pwsh.subprocess.run",
                 return_value=completed)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-r2", action="restore", drive_id="d1",
                   item_id="i1", args={}, dry_run_result="")
    cfg = _stub_cfg(tmp_path)
    result = execute_restore(op, _client(handler), logger,
                             before={"parent_path": "/Shared Documents/_fazla_smoke",
                                     "name": "hello.txt"},
                             cfg=cfg)

    assert result.status == "error"
    assert "no match" in result.error
    # Guard against regression that re-wraps PS stderr with the legacy
    # _ODFB_RESTORE_MANUAL text; that phrase is unique to the manual wrap.
    assert "Graph v1.0 /restore" not in result.error
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-r2"]
    assert entries[-1]["result"] == "error"
