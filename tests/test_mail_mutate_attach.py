import base64
import hashlib
from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.attach import (
    execute_add_attachment_small,
    execute_remove_attachment,
    pick_upload_strategy,
)


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_pick_upload_strategy_under_3mb_returns_small():
    assert pick_upload_strategy(size=1024) == "small"
    assert pick_upload_strategy(size=3 * 1024 * 1024 - 1) == "small"


def test_pick_upload_strategy_3mb_exact_returns_large():
    assert pick_upload_strategy(size=3 * 1024 * 1024) == "large"


def test_pick_upload_strategy_above_3mb_returns_large():
    assert pick_upload_strategy(size=10 * 1024 * 1024) == "large"


def test_add_small_posts_base64_inline(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "att-new", "name": "x.pdf", "size": 5}
    logger = _logger(tmp_path)
    file_bytes = b"hello"
    op = Operation(
        op_id="op-a", action="mail.attach.add",
        drive_id="me", item_id="m1",
        args={
            "name": "x.pdf",
            "content_type": "application/pdf",
            "content_bytes_b64": base64.b64encode(file_bytes).decode("ascii"),
        },
    )
    result = execute_add_attachment_small(op, graph, logger, before={})
    assert result.status == "ok"
    assert result.after["id"] == "att-new"
    assert result.after["name"] == "x.pdf"
    assert result.after["size"] == 5
    assert result.after["content_hash"] == hashlib.sha256(file_bytes).hexdigest()

    assert graph.post.call_args.args[0] == "/me/messages/m1/attachments"
    body = graph.post.call_args.kwargs["json"]
    assert body["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert body["name"] == "x.pdf"
    assert body["contentType"] == "application/pdf"
    assert body["contentBytes"] == base64.b64encode(file_bytes).decode("ascii")

    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-attach-add"


def test_add_small_app_only_routing(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "a2", "name": "y", "size": 1}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-ao", action="mail.attach.add",
        drive_id="bob@example.com", item_id="m1",
        args={
            "name": "y", "content_type": "text/plain",
            "content_bytes_b64": base64.b64encode(b"X").decode("ascii"),
            "auth_mode": "app-only",
        },
    )
    execute_add_attachment_small(op, graph, logger, before={})
    assert graph.post.call_args.args[0] == "/users/bob@example.com/messages/m1/attachments"


def test_remove_attachment_deletes_and_records_before_bytes(tmp_path):
    graph = MagicMock()
    graph.delete.return_value = None
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-rm", action="mail.attach.remove",
        drive_id="me", item_id="m1",
        args={"attachment_id": "att-1"},
    )
    prior = {
        "id": "att-1", "name": "report.pdf", "content_type": "application/pdf",
        "size": 1234,
        "content_bytes_b64": base64.b64encode(b"pdf-bytes").decode("ascii"),
    }
    result = execute_remove_attachment(op, graph, logger, before=prior)
    assert result.status == "ok"
    assert graph.delete.call_args.args[0] == "/me/messages/m1/attachments/att-1"
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-attach-remove"
    assert entries[0]["before"]["content_bytes_b64"]


def test_remove_attachment_graph_error(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.delete.side_effect = GraphError("not found")
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-err", action="mail.attach.remove",
        drive_id="me", item_id="m1",
        args={"attachment_id": "missing"},
    )
    result = execute_remove_attachment(op, graph, logger, before={})
    assert result.status == "error"
