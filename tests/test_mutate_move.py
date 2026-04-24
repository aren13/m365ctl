from __future__ import annotations

import httpx

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.onedrive.mutate.move import execute_move
from m365ctl.common.planfile import Operation


def _op(**over) -> Operation:
    base = dict(
        op_id="op-1", action="move", drive_id="d1", item_id="i1",
        args={"new_parent_item_id": "NEWPARENT"},
        dry_run_result="would move /A/x -> /B/x",
    )
    base.update(over)
    return Operation(**base)


def test_execute_move_issues_patch_and_logs_both_phases(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content.decode()))
        return httpx.Response(
            200,
            json={
                "id": "i1", "name": "x",
                "parentReference": {"id": "NEWPARENT", "path": "/drive/root:/B"},
            },
        )

    from m365ctl.common.graph import GraphClient
    client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    result = execute_move(
        _op(),
        client,
        logger,
        before={"parent_path": "/A", "name": "x"},
    )

    assert result.status == "ok"
    assert calls[0][0] == "PATCH"
    assert "NEWPARENT" in calls[0][2]

    entries = list(iter_audit_entries(logger))
    phases = [e["phase"] for e in entries if e["op_id"] == "op-1"]
    assert phases == ["start", "end"]


def test_execute_move_start_record_persists_even_if_graph_raises(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"error": {"code": "accessDenied", "message": "no"}}
        )

    from m365ctl.common.graph import GraphClient
    client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    result = execute_move(_op(), client, logger,
                          before={"parent_path": "/A", "name": "x"})
    assert result.status == "error"
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-1"]
    assert [e["phase"] for e in entries] == ["start", "end"]
    assert entries[1]["result"] == "error"
    assert "accessDenied" in entries[1]["error"]
