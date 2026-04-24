from __future__ import annotations

import httpx

from fazla_od.audit import AuditLogger, iter_audit_entries
from fazla_od.graph import GraphClient
from fazla_od.mutate.delete import execute_recycle_delete, execute_restore
from fazla_od.planfile import Operation


def _client(handler):
    return GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
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
