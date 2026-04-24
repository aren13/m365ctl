from __future__ import annotations

import httpx

from m365ctl.audit import AuditLogger, iter_audit_entries
from m365ctl.graph import GraphClient
from m365ctl.mutate.copy import execute_copy
from m365ctl.planfile import Operation


def test_execute_copy_polls_location_until_complete(tmp_path):
    seq = iter([
        httpx.Response(202, headers={"Location": "https://graph/monitor/job1"}, json={}),
        httpx.Response(200, json={"status": "inProgress", "percentageComplete": 50}),
        httpx.Response(200, json={"status": "completed",
                                  "resourceId": "NEW-ITEM-ID",
                                  "resourceLocation":
                                      "https://graph.microsoft.com/v1.0/drives/d2/items/NEW-ITEM-ID"}),
    ])

    def handler(request):
        return next(seq)

    client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-1", action="copy", drive_id="d1", item_id="i1",
                   args={"target_drive_id": "d2", "target_parent_item_id": "P",
                         "new_name": "copy.txt"},
                   dry_run_result="")
    result = execute_copy(op, client, logger,
                          before={"parent_path": "/A", "name": "x.txt"},
                          poll_interval=0.0, max_wait_seconds=5)

    assert result.status == "ok"
    assert result.after["new_item_id"] == "NEW-ITEM-ID"
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-1"]
    assert entries[-1]["after"]["new_item_id"] == "NEW-ITEM-ID"


def test_execute_copy_times_out(tmp_path):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(202,
                                  headers={"Location": "https://graph/monitor/j"},
                                  json={})
        return httpx.Response(200, json={"status": "inProgress",
                                         "percentageComplete": 10})

    client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-2", action="copy", drive_id="d1", item_id="i1",
                   args={"target_drive_id": "d2", "target_parent_item_id": "P",
                         "new_name": "y.txt"},
                   dry_run_result="")
    result = execute_copy(op, client, logger,
                          before={"parent_path": "/", "name": "x"},
                          poll_interval=0.0, max_wait_seconds=0.0)
    assert result.status == "error"
    assert "timeout" in result.error.lower()
