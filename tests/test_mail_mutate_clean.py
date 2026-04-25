"""Tests for mail.mutate.clean — hard-delete + empty-folder/recycle-bin.

Each scenario from Phase 6 plan G1:
1. ``execute_hard_delete`` writes EML to <purged_dir>/<YYYY-MM-DD>/<op_id>.eml
   BEFORE calling ``graph.delete`` (assert ordering).
2. Audit ``before`` block contains internet_message_id, subject,
   sender_address, purged_eml_path.
3. On ``graph.delete`` failure, the EML capture file remains on disk.
4. On ``fetch_eml_bytes`` failure, return ``status="error"`` and skip delete.
5. ``execute_empty_folder`` lists messages, captures each EML, then deletes.
6. ``execute_empty_recycle_bin`` forwards to ``execute_empty_folder`` with
   folder_id="deleteditems".
7. Per-message failures during empty-folder don't abort the loop;
   final result has ``status="error"`` if any failed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.graph import GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.clean import (
    execute_empty_folder,
    execute_empty_recycle_bin,
    execute_hard_delete,
)


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _eml_bytes(msg_id: str = "<abc@example.com>", subject: str = "hi",
               sender: str = "sender@example.com") -> bytes:
    return (
        f"Message-ID: {msg_id}\r\n"
        f"Subject: {subject}\r\n"
        f"From: {sender}\r\n"
        f"To: me@example.com\r\n"
        f"\r\n"
        f"body\r\n"
    ).encode("utf-8")


def _hard_delete_op(op_id: str = "op-hd") -> Operation:
    return Operation(
        op_id=op_id, action="mail.delete.hard",
        drive_id="me", item_id="m1",
        args={
            "mailbox_spec": "me",
            "auth_mode": "delegated",
            "message_id": "m1",
        },
    )


def _empty_folder_op(op_id: str = "op-ef", folder_id: str = "AAA=") -> Operation:
    return Operation(
        op_id=op_id, action="mail.empty.folder",
        drive_id="me", item_id=folder_id,
        args={
            "mailbox_spec": "me",
            "auth_mode": "delegated",
            "folder_id": folder_id,
        },
    )


def test_hard_delete_writes_eml_before_graph_delete(tmp_path):
    purged = tmp_path / "purged"
    eml = _eml_bytes()
    graph = MagicMock()
    graph.get_bytes.return_value = eml
    graph.delete.return_value = None

    op = _hard_delete_op("op-1")
    result = execute_hard_delete(
        op, graph, _logger(tmp_path), purged_dir=purged, before={},
    )

    assert result.status == "ok"
    expected = purged / _today() / "op-1.eml"
    assert expected.exists()
    assert expected.read_bytes() == eml

    # Order check via mock_calls: get_bytes (capture) must precede delete.
    method_calls = [c for c in graph.mock_calls
                    if c[0] in ("get_bytes", "delete")]
    assert method_calls[0][0] == "get_bytes"
    assert method_calls[-1][0] == "delete"
    # exact arg check on delete
    assert graph.delete.call_args.args[0] == "/me/messages/m1"


def test_hard_delete_audit_before_contains_summary_and_path(tmp_path):
    purged = tmp_path / "purged"
    eml = _eml_bytes(
        msg_id="<abc-123@example.com>",
        subject="quarterly review",
        sender="boss@example.com",
    )
    graph = MagicMock()
    graph.get_bytes.return_value = eml
    graph.delete.return_value = None

    logger = _logger(tmp_path)
    op = _hard_delete_op("op-aud")
    execute_hard_delete(op, graph, logger, purged_dir=purged, before={})

    entries = list(iter_audit_entries(logger))
    starts = [e for e in entries if e["phase"] == "start"]
    assert starts, "no start entry"
    before = starts[0]["before"]
    assert before["internet_message_id"] == "<abc-123@example.com>"
    assert before["subject"] == "quarterly review"
    assert before["sender_address"] == "boss@example.com"
    expected = purged / _today() / "op-aud.eml"
    assert before["purged_eml_path"] == str(expected)


def test_hard_delete_preserves_eml_when_graph_delete_fails(tmp_path):
    purged = tmp_path / "purged"
    eml = _eml_bytes()
    graph = MagicMock()
    graph.get_bytes.return_value = eml
    graph.delete.side_effect = GraphError("500 server error")

    op = _hard_delete_op("op-fail")
    result = execute_hard_delete(
        op, graph, _logger(tmp_path), purged_dir=purged, before={},
    )

    assert result.status == "error"
    assert "500 server error" in (result.error or "")
    eml_path = purged / _today() / "op-fail.eml"
    assert eml_path.exists()
    assert eml_path.read_bytes() == eml


def test_hard_delete_skips_delete_when_eml_capture_fails(tmp_path):
    purged = tmp_path / "purged"
    graph = MagicMock()
    graph.get_bytes.side_effect = GraphError("404 not found")

    op = _hard_delete_op("op-no-eml")
    logger = _logger(tmp_path)
    result = execute_hard_delete(
        op, graph, logger, purged_dir=purged, before={},
    )

    assert result.status == "error"
    assert "EML capture failed" in (result.error or "")
    graph.delete.assert_not_called()
    # No partial EML on disk
    eml_path = purged / _today() / "op-no-eml.eml"
    assert not eml_path.exists()
    # Audit log still recorded the attempt.
    entries = list(iter_audit_entries(logger))
    assert any(e["phase"] == "start" for e in entries)
    assert any(e["phase"] == "end" and e["result"] == "error" for e in entries)


def test_empty_folder_captures_each_eml_then_deletes(tmp_path):
    purged = tmp_path / "purged"
    graph = MagicMock()
    # One page of two messages.
    graph.get_paginated.return_value = iter([
        ([{"id": "m-A"}, {"id": "m-B"}], None),
    ])
    graph.get_bytes.side_effect = [
        _eml_bytes(msg_id="<A@x>", subject="A"),
        _eml_bytes(msg_id="<B@x>", subject="B"),
    ]
    graph.delete.return_value = None

    op = _empty_folder_op("op-ef-1", folder_id="AAA=")
    result = execute_empty_folder(
        op, graph, _logger(tmp_path), purged_dir=purged, before={},
    )

    assert result.status == "ok"
    assert result.after["purged_count"] == 2
    capture_root = purged / _today() / "op-ef-1"
    assert (capture_root / "m-A.eml").exists()
    assert (capture_root / "m-B.eml").exists()

    # listing call
    list_call_args = graph.get_paginated.call_args
    assert list_call_args.args[0] == "/me/mailFolders/AAA=/messages"

    # capture-then-delete ordering for each message
    method_seq = [c[0] for c in graph.mock_calls
                  if c[0] in ("get_bytes", "delete")]
    # Expect: get_bytes(A), delete(A), get_bytes(B), delete(B)
    assert method_seq == ["get_bytes", "delete", "get_bytes", "delete"]
    delete_paths = [c.args[0] for c in graph.delete.call_args_list]
    assert delete_paths == ["/me/messages/m-A", "/me/messages/m-B"]


def test_empty_recycle_bin_forwards_to_empty_folder_with_deleteditems(tmp_path):
    purged = tmp_path / "purged"
    graph = MagicMock()
    graph.get_paginated.return_value = iter([([{"id": "m-X"}], None)])
    graph.get_bytes.return_value = _eml_bytes(msg_id="<X@x>", subject="X")
    graph.delete.return_value = None

    op = Operation(
        op_id="op-rb", action="mail.empty.recycle-bin",
        drive_id="me", item_id="",
        args={"mailbox_spec": "me", "auth_mode": "delegated"},
    )
    result = execute_empty_recycle_bin(
        op, graph, _logger(tmp_path), purged_dir=purged, before={},
    )

    assert result.status == "ok"
    # listing path must target the well-known deleteditems folder.
    assert (graph.get_paginated.call_args.args[0]
            == "/me/mailFolders/deleteditems/messages")


def test_empty_folder_continues_on_per_message_failure(tmp_path):
    purged = tmp_path / "purged"
    graph = MagicMock()
    graph.get_paginated.return_value = iter([
        ([{"id": "m-OK"}, {"id": "m-BAD"}, {"id": "m-OK2"}], None),
    ])
    # First capture succeeds, second raises (skipped), third succeeds.
    graph.get_bytes.side_effect = [
        _eml_bytes(msg_id="<OK@x>"),
        GraphError("404 gone"),
        _eml_bytes(msg_id="<OK2@x>"),
    ]
    graph.delete.return_value = None

    op = _empty_folder_op("op-ef-mix")
    result = execute_empty_folder(
        op, graph, _logger(tmp_path), purged_dir=purged, before={},
    )

    # Loop did not abort; OK and OK2 were captured + deleted.
    assert result.status == "error"
    assert result.after["purged_count"] == 2
    assert len(result.after["failures"]) == 1
    assert "m-BAD" in result.after["failures"][0]

    delete_paths = [c.args[0] for c in graph.delete.call_args_list]
    # m-BAD was skipped (capture failed), only OK + OK2 were deleted.
    assert delete_paths == ["/me/messages/m-OK", "/me/messages/m-OK2"]
