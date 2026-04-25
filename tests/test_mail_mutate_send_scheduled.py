from __future__ import annotations

from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.mutate.send import execute_send_scheduled


def _op(*, schedule_at: str = "2026-05-01T09:00:00+00:00") -> Operation:
    return Operation(
        op_id=new_op_id(),
        action="mail.send.scheduled",
        drive_id="me",
        item_id="draft-1",
        args={"auth_mode": "delegated", "schedule_at": schedule_at},
        dry_run_result="",
    )


def test_patches_extended_property_then_sends(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "draft-1"}
    logger = AuditLogger(ops_dir=tmp_path / "ops")

    op = _op()
    r = execute_send_scheduled(op, graph, logger, before={})

    assert r.status == "ok"

    # PATCH first, then POST /send.
    assert graph.method_calls[0][0] == "patch"
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {
        "singleValueExtendedProperties": [
            {"id": "SystemTime 0x3FEF", "value": "2026-05-01T09:00:00+00:00"},
        ],
    }
    assert "/messages/draft-1" in graph.patch.call_args.args[0]

    assert graph.method_calls[1][0] == "post_raw"
    assert "/messages/draft-1/send" in graph.post_raw.call_args.args[0]


def test_app_only_routes_via_users_upn(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "draft-1"}
    logger = AuditLogger(ops_dir=tmp_path / "ops")

    op = Operation(
        op_id=new_op_id(),
        action="mail.send.scheduled",
        drive_id="bob@example.com",
        item_id="draft-1",
        args={"auth_mode": "app-only",
              "schedule_at": "2026-05-01T09:00:00+00:00"},
        dry_run_result="",
    )
    execute_send_scheduled(op, graph, logger, before={})
    assert "/users/bob@example.com/messages/draft-1" in graph.patch.call_args.args[0]


def test_patch_failure_aborts_send(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.patch.side_effect = GraphError("BadRequest: invalid extended property")
    logger = AuditLogger(ops_dir=tmp_path / "ops")

    op = _op()
    r = execute_send_scheduled(op, graph, logger, before={})

    assert r.status == "error"
    assert "BadRequest" in (r.error or "")
    graph.post_raw.assert_not_called()


def test_send_failure_after_patch(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.patch.return_value = {"id": "draft-1"}
    graph.post_raw.side_effect = GraphError("Forbidden")
    logger = AuditLogger(ops_dir=tmp_path / "ops")

    op = _op()
    r = execute_send_scheduled(op, graph, logger, before={})

    assert r.status == "error"
    assert "Forbidden" in (r.error or "")
    # PATCH happened first; the extended property is set on the draft even
    # though send failed. Operator can re-send with `mail send <draft>`.
    graph.patch.assert_called_once()


def test_records_schedule_at_in_audit_after(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "draft-1"}
    logger = AuditLogger(ops_dir=tmp_path / "ops")

    op = _op()
    r = execute_send_scheduled(op, graph, logger, before={})

    assert r.status == "ok"
    assert r.after.get("schedule_at") == "2026-05-01T09:00:00+00:00"
