from __future__ import annotations

import httpx

from fazla_od.audit import AuditLogger, iter_audit_entries
from fazla_od.graph import GraphClient
from fazla_od.mutate.rename import execute_rename
from fazla_od.planfile import Operation


def test_execute_rename_issues_patch_with_new_name(tmp_path):
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "i1", "name": "new.txt"})

    client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-1", action="rename", drive_id="d1", item_id="i1",
                   args={"new_name": "new.txt"}, dry_run_result="")
    result = execute_rename(op, client, logger,
                            before={"parent_path": "/", "name": "old.txt"})
    assert result.status == "ok"
    assert '"name":"new.txt"' in captured["body"].replace(" ", "")
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-1"]
    assert entries[-1]["after"]["name"] == "new.txt"
