from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from m365ctl.onedrive.cli.move import run_move
from m365ctl.common.config import Config, ScopeConfig
from m365ctl.common.safety import (
    ScopeViolation,
    assert_scope_allowed,
    filter_by_scope,
)


@dataclass(frozen=True)
class _Item:
    drive_id: str
    item_id: str
    full_path: str
    name: str = ""


def _cfg(
    *,
    allow: list[str] = None,
    deny: list[str] = None,
    unsafe_requires_flag: bool = True,
    tmp_path: Path = None,
) -> Config:
    scope = ScopeConfig(
        allow_drives=allow or ["d1"],
        allow_users=["*"],
        deny_paths=deny or [],
        unsafe_requires_flag=unsafe_requires_flag,
    )
    # Only the .scope field matters here; stub the rest.
    from m365ctl.common.config import CatalogConfig, LoggingConfig
    return Config(
        tenant_id="t", client_id="c",
        cert_path=(tmp_path or Path("/tmp")) / "k",
        cert_public=(tmp_path or Path("/tmp")) / "c",
        default_auth="app-only",
        scope=scope,
        catalog=CatalogConfig(path=(tmp_path or Path("/tmp")) / "x.duckdb"),
        logging=LoggingConfig(ops_dir=(tmp_path or Path("/tmp")) / "logs"),
    )


def test_allow_drives_permits_listed_drive(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    item = _Item(drive_id="d1", item_id="i", full_path="/foo")
    assert_scope_allowed(item, cfg, unsafe_scope=False)  # no raise


def test_allow_drives_blocks_unlisted_drive(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    item = _Item(drive_id="OTHER", item_id="i", full_path="/foo")
    with pytest.raises(ScopeViolation, match="drive"):
        assert_scope_allowed(item, cfg, unsafe_scope=False)


def test_deny_paths_block_matching_item(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], deny=["/Confidential/**"], tmp_path=tmp_path)
    item = _Item(drive_id="d1", item_id="i", full_path="/Confidential/secret.docx")
    with pytest.raises(ScopeViolation, match="deny"):
        assert_scope_allowed(item, cfg, unsafe_scope=False)


def test_filter_by_scope_drops_denied_items(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], deny=["/HR/**"], tmp_path=tmp_path)
    items = [
        _Item(drive_id="d1", item_id="a", full_path="/Public/report.pdf"),
        _Item(drive_id="d1", item_id="b", full_path="/HR/salaries.xlsx"),
        _Item(drive_id="d1", item_id="c", full_path="/HR"),  # exact match to parent
    ]
    kept = list(filter_by_scope(items, cfg, unsafe_scope=False))
    assert [i.item_id for i in kept] == ["a"]


def test_filter_by_scope_drops_items_outside_allow_drives(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    items = [
        _Item(drive_id="d1",    item_id="a", full_path="/p"),
        _Item(drive_id="OTHER", item_id="b", full_path="/p"),
    ]
    kept = list(filter_by_scope(items, cfg, unsafe_scope=False))
    assert [i.item_id for i in kept] == ["a"]


def test_unsafe_scope_bypasses_allow_list_with_tty_yes(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    item = _Item(drive_id="OTHER", item_id="i", full_path="/foo")
    with patch("m365ctl.common.safety._confirm_via_tty", return_value=True):
        assert_scope_allowed(item, cfg, unsafe_scope=True)  # no raise


def test_unsafe_scope_without_tty_yes_still_raises(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    item = _Item(drive_id="OTHER", item_id="i", full_path="/foo")
    with patch("m365ctl.common.safety._confirm_via_tty", return_value=False):
        with pytest.raises(ScopeViolation, match="declined"):
            assert_scope_allowed(item, cfg, unsafe_scope=True)


def test_unsafe_scope_flag_required_per_config(tmp_path: Path) -> None:
    """If unsafe_requires_flag is True (default), passing unsafe_scope=False
    against an out-of-scope item always raises — no TTY prompt offered."""
    cfg = _cfg(allow=["d1"], unsafe_requires_flag=True, tmp_path=tmp_path)
    item = _Item(drive_id="OTHER", item_id="i", full_path="/foo")
    with patch("m365ctl.common.safety._confirm_via_tty") as m:
        with pytest.raises(ScopeViolation):
            assert_scope_allowed(item, cfg, unsafe_scope=False)
        m.assert_not_called()  # never prompted — flag required upfront


# ----------------------------------------------------------------- safety invariants
# Each test below cross-references one safety rule (dry-run-by-default,
# scope allow-listing, plan-file workflow, audit-log capture, undo
# round-trip). The rules themselves are summarised in AGENTS.md
# "Safety envelope" — the original design-spec table they were lifted
# from no longer ships in the repo.


def test_dry_run_is_default_no_mutation(tmp_path, mocker):
    """Spec §7 rule 1: mutating command without --confirm issues zero Graph calls."""
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    mocker.patch("m365ctl.onedrive.cli.move.load_config", return_value=cfg)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={})

    from m365ctl.common.graph import GraphClient
    client = GraphClient(token_provider=lambda: "t",
                         transport=httpx.MockTransport(handler),
                         sleep=lambda s: None)
    mocker.patch("m365ctl.onedrive.cli.move.build_graph_client", return_value=client)
    mocker.patch(
        "m365ctl.onedrive.cli.move._lookup_item",
        return_value={"drive_id": "d1", "item_id": "i1",
                      "full_path": "/x", "name": "x", "parent_path": "/"},
    )

    rc = run_move(
        config_path=tmp_path / "c.toml",
        scope="drive:d1", drive_id="d1", item_id="i1",
        pattern=None, from_plan=None,
        new_parent_path="/B", new_parent_item_id="PB",
        plan_out=None, confirm=False, unsafe_scope=False,
    )
    assert rc == 0
    # _lookup_item is mocked, so it never hits the transport.
    # Zero transport calls total: no GET for lookup, no PATCH/POST/DELETE for mutations.
    assert calls["n"] == 0


def test_pattern_plus_confirm_is_rejected(tmp_path, mocker, capsys):
    """Spec §7 rule 2: bulk destructive requires plan file."""
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    mocker.patch("m365ctl.onedrive.cli.move.load_config", return_value=cfg)
    rc = run_move(
        config_path=tmp_path / "c.toml",
        scope="drive:d1", drive_id=None, item_id=None,
        pattern="**/*.tmp", from_plan=None,
        new_parent_path="/T", new_parent_item_id="T",
        plan_out=None, confirm=True, unsafe_scope=False,
    )
    assert rc == 2
    assert "plan" in capsys.readouterr().err.lower()


def test_from_plan_no_glob_reexpansion_exact_call_count(tmp_path, mocker):
    """Spec §7 rule 2: --from-plan does NOT re-expand globs."""
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    mocker.patch("m365ctl.onedrive.cli.move.load_config", return_value=cfg)

    from m365ctl.onedrive.catalog.db import open_catalog
    with open_catalog(cfg.catalog.path) as conn:
        for i in range(100):
            conn.execute(
                "INSERT INTO items (drive_id, item_id, name, full_path, "
                "parent_path, is_folder, is_deleted) VALUES "
                "(?, ?, ?, ?, ?, false, false)",
                ["d1", f"i{i}", f"x{i}.tmp", f"/junk/x{i}.tmp", "/junk"],
            )

    patches = {"n": 0}

    def handler(request):
        body = json.loads(request.read())
        responses = []
        for r in body["requests"]:
            if r["method"] == "PATCH":
                patches["n"] += 1
                responses.append({
                    "id": r["id"], "status": 200, "headers": {},
                    "body": {"id": "x", "name": "x",
                             "parentReference": {"id": "P", "path": "/drive/root:/B"}},
                })
            else:
                # GET — phase-0 metadata
                item_id = r["url"].split("/items/")[1].split("?")[0]
                responses.append({
                    "id": r["id"], "status": 200, "headers": {},
                    "body": {"id": item_id, "name": item_id,
                             "parentReference": {"id": "P", "path": "/drive/root:/junk"}},
                })
        return httpx.Response(200, json={"responses": responses})

    from m365ctl.common.graph import GraphClient
    client = GraphClient(token_provider=lambda: "t",
                         transport=httpx.MockTransport(handler),
                         sleep=lambda s: None)
    mocker.patch("m365ctl.onedrive.cli.move.build_graph_client", return_value=client)

    from m365ctl.common.planfile import PLAN_SCHEMA_VERSION
    plan_payload = {
        "version": PLAN_SCHEMA_VERSION,
        "created_at": "2026-04-24T10:00:00+00:00",
        "source_cmd": "od-move --pattern '/junk/**' ...",
        "scope": "drive:d1",
        "operations": [
            {"op_id": f"op-{i}", "action": "move",
             "drive_id": "d1", "item_id": f"i{i}",
             "args": {"new_parent_item_id": "PB"},
             "dry_run_result": ""} for i in range(2)
        ],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan_payload))

    rc = run_move(
        config_path=tmp_path / "c.toml",
        scope=None, drive_id=None, item_id=None, pattern=None,
        from_plan=plan_path,
        new_parent_path=None, new_parent_item_id=None,
        plan_out=None, confirm=True, unsafe_scope=False,
    )
    assert rc == 0
    assert patches["n"] == 2  # NOT 100

    # Additional regression: passing both --pattern AND --from-plan must still
    # NOT re-expand. The from-plan path should short-circuit before pattern
    # is examined.
    rc2 = run_move(
        config_path=tmp_path / "c.toml",
        scope=None, drive_id=None, item_id=None,
        pattern="**/*",  # would match all 100 catalog rows if re-expanded
        from_plan=plan_path,
        new_parent_path=None, new_parent_item_id=None,
        plan_out=None, confirm=True, unsafe_scope=False,
    )
    assert rc2 == 0
    assert patches["n"] == 4  # 2 + 2 = 4 total; NOT 2 + 100 = 102


def test_piped_stdin_cannot_auto_confirm_unsafe_scope(tmp_path, monkeypatch):
    """Spec §7 rule 3: /dev/tty, not stdin, drives the unsafe-scope confirm."""
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    item = _Item(drive_id="OTHER", item_id="i", full_path="/foo")

    monkeypatch.setattr("sys.stdin", io.StringIO("y\ny\ny\n"))
    real_open = open

    def fake_open(path, *a, **kw):
        if path == "/dev/tty":
            raise OSError("no controlling tty")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", fake_open)

    with pytest.raises(ScopeViolation, match="declined"):
        assert_scope_allowed(item, cfg, unsafe_scope=True)


def test_deny_paths_never_appear_in_plan_or_tsv(tmp_path, mocker, capsys):
    """Spec §7 rule 4: deny-paths filtered BEFORE plan emission."""
    cfg = _cfg(allow=["d1"], deny=["/Confidential/**"], tmp_path=tmp_path)
    mocker.patch("m365ctl.onedrive.cli.move.load_config", return_value=cfg)

    from m365ctl.onedrive.catalog.db import open_catalog
    with open_catalog(cfg.catalog.path) as conn:
        conn.execute(
            "INSERT INTO items (drive_id, item_id, name, full_path, "
            "parent_path, is_folder, is_deleted) VALUES "
            "('d1','ok','pub.txt','/Public/pub.txt','/Public',false,false),"
            "('d1','no','sec.docx','/Confidential/sec.docx','/Confidential',false,false)"
        )

    plan_path = tmp_path / "plan.json"
    rc = run_move(
        config_path=tmp_path / "c.toml",
        scope="drive:d1", drive_id=None, item_id=None,
        pattern="/*/*",
        from_plan=None,
        new_parent_path="/Elsewhere", new_parent_item_id="X",
        plan_out=plan_path, confirm=False, unsafe_scope=False,
    )
    assert rc == 0
    plan = json.loads(plan_path.read_text())
    names = [op["item_id"] for op in plan["operations"]]
    assert "ok" in names
    assert "no" not in names


def test_audit_start_line_persists_even_on_mid_mutation_crash(tmp_path):
    """Spec §7 rule 5: audit 'start' is written BEFORE the Graph call."""
    from m365ctl.common.audit import AuditLogger, iter_audit_entries
    from m365ctl.onedrive.mutate.move import execute_move
    from m365ctl.common.planfile import Operation

    def handler(request):
        raise httpx.ConnectError("connection reset by peer")

    from m365ctl.common.graph import GraphClient
    client = GraphClient(token_provider=lambda: "t",
                         transport=httpx.MockTransport(handler),
                         sleep=lambda s: None)
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="CRASH", action="move", drive_id="d1", item_id="i1",
                   args={"new_parent_item_id": "P"}, dry_run_result="")

    with pytest.raises(httpx.ConnectError):
        execute_move(op, client, logger,
                     before={"parent_path": "/A", "name": "x"})

    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "CRASH"]
    # Start record must be on disk; no end record because the crash propagated
    # before log_mutation_end could run.
    assert len(entries) == 1
    assert entries[0]["phase"] == "start"
