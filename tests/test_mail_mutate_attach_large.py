from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.mutate.attach import (
    CHUNK_SIZE_BYTES, execute_add_attachment_large,
)


def _op(file_path: Path, *, size: int, content_type: str = "application/octet-stream") -> Operation:
    return Operation(
        op_id=new_op_id(),
        action="mail.attach.add.large",
        drive_id="me",
        item_id="msg-1",
        args={
            "name": file_path.name,
            "content_type": content_type,
            "size": size,
            "file_path": str(file_path),
            "auth_mode": "delegated",
        },
        dry_run_result="",
    )


def _make_file(tmp_path: Path, *, size: int) -> Path:
    p = tmp_path / "big.bin"
    p.write_bytes(b"x" * size)
    return p


def test_creates_upload_session_then_streams_chunks(tmp_path):
    size = 4 * 1024 * 1024 + 100   # 4MB + 100 bytes → 2 chunks
    fp = _make_file(tmp_path, size=size)
    graph = MagicMock()
    graph.post.return_value = {
        "uploadUrl": "https://upload.graph.microsoft.com/sessions/abc",
        "expirationDateTime": "2026-05-01T00:00:00Z",
    }
    # First PUT returns 'next expected', last returns the attachment dict.
    graph.put_chunk.side_effect = [
        ({}, 202),  # accepted; more to upload
        ({"id": "att-1", "name": fp.name, "size": size}, 201),
    ]
    logger = AuditLogger(ops_dir=tmp_path / "ops")

    op = _op(fp, size=size)
    r = execute_add_attachment_large(op, graph, logger, before={})

    assert r.status == "ok"
    assert r.after.get("id") == "att-1"

    # createUploadSession path
    create_call = graph.post.call_args
    assert "createUploadSession" in create_call.args[0]
    body = create_call.kwargs["json"]
    assert body == {
        "AttachmentItem": {
            "attachmentType": "file",
            "name": fp.name,
            "size": size,
            "contentType": "application/octet-stream",
        }
    }

    # Two PUT calls.
    assert graph.put_chunk.call_count == 2
    first_kwargs = graph.put_chunk.call_args_list[0].kwargs
    assert first_kwargs["content_range"] == f"bytes 0-{CHUNK_SIZE_BYTES - 1}/{size}"
    second_kwargs = graph.put_chunk.call_args_list[1].kwargs
    assert second_kwargs["content_range"] == f"bytes {CHUNK_SIZE_BYTES}-{size - 1}/{size}"


def test_single_chunk_when_file_smaller_than_chunk(tmp_path):
    size = 100_000
    fp = _make_file(tmp_path, size=size)
    graph = MagicMock()
    graph.post.return_value = {"uploadUrl": "https://upload/abc"}
    graph.put_chunk.return_value = ({"id": "att-1", "size": size}, 201)
    logger = AuditLogger(ops_dir=tmp_path / "ops")

    op = _op(fp, size=size)
    r = execute_add_attachment_large(op, graph, logger, before={})
    assert r.status == "ok"
    assert graph.put_chunk.call_count == 1
    only = graph.put_chunk.call_args.kwargs
    assert only["content_range"] == f"bytes 0-{size - 1}/{size}"


def test_app_only_routes_via_users_upn(tmp_path):
    size = 4 * 1024 * 1024
    fp = _make_file(tmp_path, size=size)
    graph = MagicMock()
    graph.post.return_value = {"uploadUrl": "https://upload/abc"}
    graph.put_chunk.return_value = ({"id": "att-1", "size": size}, 201)
    logger = AuditLogger(ops_dir=tmp_path / "ops")

    op = Operation(
        op_id=new_op_id(),
        action="mail.attach.add.large",
        drive_id="bob@example.com",
        item_id="msg-1",
        args={
            "name": fp.name, "content_type": "application/octet-stream",
            "size": size, "file_path": str(fp), "auth_mode": "app-only",
        },
        dry_run_result="",
    )
    execute_add_attachment_large(op, graph, logger, before={})
    assert "/users/bob@example.com/messages/msg-1/attachments/createUploadSession" in graph.post.call_args.args[0]


def test_create_session_failure_aborts(tmp_path):
    from m365ctl.common.graph import GraphError
    size = 4 * 1024 * 1024
    fp = _make_file(tmp_path, size=size)
    graph = MagicMock()
    graph.post.side_effect = GraphError("BadRequest")
    logger = AuditLogger(ops_dir=tmp_path / "ops")

    op = _op(fp, size=size)
    r = execute_add_attachment_large(op, graph, logger, before={})
    assert r.status == "error"
    graph.put_chunk.assert_not_called()


def test_chunk_failure_returns_error_with_progress(tmp_path):
    from m365ctl.common.graph import GraphError
    size = 4 * 1024 * 1024 + 100
    fp = _make_file(tmp_path, size=size)
    graph = MagicMock()
    graph.post.return_value = {"uploadUrl": "https://upload/abc"}
    graph.put_chunk.side_effect = [
        ({}, 202),                      # first chunk OK
        GraphError("uploadSession fragment failed"),
    ]
    logger = AuditLogger(ops_dir=tmp_path / "ops")

    op = _op(fp, size=size)
    r = execute_add_attachment_large(op, graph, logger, before={})
    assert r.status == "error"
    assert "fragment failed" in (r.error or "")
    # First chunk was uploaded; the audit log records the upload session
    # URL so the operator can inspect / resume manually.
    assert graph.put_chunk.call_count == 2


def test_records_attachment_metadata_in_audit(tmp_path):
    size = 100_000
    fp = _make_file(tmp_path, size=size)
    graph = MagicMock()
    graph.post.return_value = {"uploadUrl": "https://upload/abc"}
    graph.put_chunk.return_value = ({"id": "att-1", "name": fp.name, "size": size}, 201)
    logger = AuditLogger(ops_dir=tmp_path / "ops")

    op = _op(fp, size=size)
    r = execute_add_attachment_large(op, graph, logger, before={})
    assert r.after == {"id": "att-1", "name": fp.name, "size": size}
