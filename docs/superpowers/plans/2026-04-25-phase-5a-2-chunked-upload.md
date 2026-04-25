# Phase 5a-2 — Chunked Attachment Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Replace the Phase 5a "deferred to 5a-2" stub for ≥3 MB attachments with the real Graph upload-session protocol. `mail attach add <msg> --file 10mb.bin --confirm` should now work end-to-end without the operator splitting the file.

**Architecture:**
- Graph upload-session flow (3 steps):
  1. `POST /{ub}/messages/{id}/attachments/createUploadSession` with body `{AttachmentItem: {attachmentType: "file", name, size, contentType}}` → returns `{uploadUrl, expirationDateTime, nextExpectedRanges}`.
  2. `PUT <uploadUrl>` with `Content-Range: bytes 0-N/total` for each chunk. Chunks must be multiples of 327680 bytes (320 KB) except the last. Default chunk size 4 MB (12 × 320 KB).
  3. Final PUT returns `{id, name, contentType, size, ...}` — the new attachment metadata.
- New `execute_add_attachment_large(op, graph, logger, *, before)` in `mail/mutate/attach.py`. Streams the file from `op.args["file_path"]` (NOT base64-buffered into args, since large files would blow memory and audit log size).
- New `GraphClient.put_chunk(url, data, *, content_range, content_length)` method — absolute URL PUT with custom headers, no auth header (the upload session URL embeds auth via signed query params).
- `mail/cli/attach.py` updated: when `pick_upload_strategy == "large"`, dispatch to `execute_add_attachment_large` with `args["file_path"]=<abs path>` instead of base64-encoding the bytes.

**Tech stack:** Existing primitives + a thin `put_chunk` helper on GraphClient. No new deps.

**Baseline:** `main` post-PR-#21 (4bc62ef), 870 passing tests, 0 mypy errors. Tag `v1.4.0`.

**Version bump:** 1.4.0 → 1.5.0.

---

## Group 1 — `put_chunk` + executor (one commit)

**Files:**
- Modify: `src/m365ctl/common/graph.py` (add `put_chunk`)
- Modify: `src/m365ctl/mail/mutate/attach.py` (add `execute_add_attachment_large`)
- Create: `tests/test_mail_mutate_attach_large.py`

### Steps

- [ ] **Step 1: Failing tests** at `tests/test_mail_mutate_attach_large.py`:

```python
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
```

- [ ] **Step 2:** Run, verify ImportError.

- [ ] **Step 3: Add `GraphClient.put_chunk`** in `src/m365ctl/common/graph.py`:

```python
def put_chunk(
    self,
    url: str,
    data: bytes,
    *,
    content_range: str,
    content_length: int,
) -> tuple[dict, int]:
    """Upload one chunk to a Graph upload-session URL.

    The upload session URL embeds auth via signed query params, so we
    DO NOT add the Authorization header. Returns ``(parsed_body, status_code)``.
    Body may be empty for 202 Accepted (more chunks expected); on 201
    Created (final chunk), it's the resulting entity (e.g. attachment).

    Raises GraphError on 4xx/5xx (mirrors the rest of GraphClient).
    """
    def _do() -> tuple[dict, int]:
        resp = self._client.put(
            url,
            content=data,
            headers={
                "Content-Range": content_range,
                "Content-Length": str(content_length),
                "Content-Type": "application/octet-stream",
            },
        )
        self._maybe_raise(resp)
        body: dict = {}
        if resp.content:
            try:
                body = resp.json()
            except Exception:  # noqa: BLE001
                body = {}
        return body, resp.status_code
    return self._retry(_do)
```

- [ ] **Step 4: Implement `execute_add_attachment_large`** — append to `src/m365ctl/mail/mutate/attach.py`:

```python
CHUNK_SIZE_BYTES = 4 * 1024 * 1024  # 4 MB; multiple of 320 KB.


def execute_add_attachment_large(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> AttachResult:
    """Upload a large attachment via Graph's upload-session protocol.

    Three Graph calls:
      1. POST createUploadSession → {uploadUrl}.
      2. PUT each chunk to uploadUrl with Content-Range header.
      3. Final PUT returns the attachment metadata.
    """
    args = op.args
    auth_mode = args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    ub = user_base(spec, auth_mode=auth_mode)
    file_path = Path(args["file_path"])
    name = args["name"]
    content_type = args.get("content_type", "application/octet-stream")
    size = int(args.get("size") or file_path.stat().st_size)

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-attach-add-large",
        args={k: v for k, v in args.items() if k != "content_bytes_b64"},
        drive_id=op.drive_id, item_id=op.item_id, before=before,
    )

    # 1. Create the upload session.
    create_path = f"{ub}/messages/{op.item_id}/attachments/createUploadSession"
    try:
        sess = graph.post(create_path, json={
            "AttachmentItem": {
                "attachmentType": "file",
                "name": name,
                "size": size,
                "contentType": content_type,
            },
        })
    except GraphError as e:
        log_mutation_end(
            logger, op_id=op.op_id, after=None, result="error", error=str(e),
        )
        return AttachResult(op_id=op.op_id, status="error", error=str(e))

    upload_url = sess["uploadUrl"]

    # 2. Stream chunks.
    final_body: dict = {}
    try:
        with file_path.open("rb") as fh:
            offset = 0
            while offset < size:
                chunk = fh.read(CHUNK_SIZE_BYTES)
                if not chunk:
                    break
                end = offset + len(chunk) - 1
                body, status = graph.put_chunk(
                    upload_url, chunk,
                    content_range=f"bytes {offset}-{end}/{size}",
                    content_length=len(chunk),
                )
                offset = end + 1
                if status in (200, 201):
                    final_body = body
                    break
    except GraphError as e:
        log_mutation_end(
            logger, op_id=op.op_id,
            after={"upload_url": upload_url, "uploaded_bytes": offset},
            result="error", error=str(e),
        )
        return AttachResult(op_id=op.op_id, status="error", error=str(e))

    after: dict[str, Any] = {
        "id": final_body.get("id", ""),
        "name": final_body.get("name", name),
        "size": final_body.get("size", size),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return AttachResult(op_id=op.op_id, status="ok", after=after)
```

Add imports as needed: `from pathlib import Path`.

- [ ] **Step 5:** Run tests, mypy + ruff clean. Commit:
```
git add src/m365ctl/common/graph.py \
        src/m365ctl/mail/mutate/attach.py \
        tests/test_mail_mutate_attach_large.py
git commit -m "feat(mail/attach): chunked upload-session executor for ≥3MB attachments"
```

---

## Group 2 — CLI integration (one commit)

Modify `src/m365ctl/mail/cli/attach.py:_run_add_attachment` to dispatch to the large executor when `pick_upload_strategy == "large"` instead of returning the deferred-stub error.

### Steps

- [ ] Tests at `tests/test_cli_mail_attach_add_large.py`:
  - `mail attach add <msg> --file <large.bin> --confirm` (where large.bin is ≥3MB) calls `execute_add_attachment_large` with `args["file_path"]=<abs path>` (NOT base64).
  - The op's `args` does NOT contain `content_bytes_b64` for the large path.
  - Without `--confirm`, prints dry-run notice with size and exits 0.
  - Validates the file exists.

- [ ] Edit `mail/cli/attach.py:_run_add_attachment`:
  - When `strategy == "large"`:
    - Don't read the bytes.
    - Build op with `args["file_path"]=str(file_path.resolve())`, `args["size"]=size`, `args["name"]`, `args["content_type"]`. No `content_bytes_b64`.
    - Call `execute_add_attachment_large(op, graph, logger, before={})`.
  - When `strategy == "small"`: existing path unchanged.

- [ ] Tests, gates, commit:
```
feat(mail/cli/attach): wire chunked upload for ≥3MB files (replaces deferred stub)
```

---

## Group 3 — Release 1.5.0

### Task 3.1: Bump + changelog + README + lockfile

- [ ] `pyproject.toml`: 1.4.0 → 1.5.0.

- [ ] Prepend CHANGELOG.md:

```markdown
## 1.5.0 — Phase 5a-2: chunked attachment upload (≥3 MB)

### Added
- `GraphClient.put_chunk(url, data, *, content_range, content_length)` —
  unauthenticated PUT to a Graph upload-session URL.
- `m365ctl.mail.mutate.attach.execute_add_attachment_large` — three-step
  upload-session flow: createUploadSession → streamed PUT chunks →
  final attachment metadata. Default chunk size 4 MB (multiple of 320 KB
  per Graph requirements).
- `mail attach add <msg> --file <≥3MB-file> --confirm` now works
  end-to-end. Replaces the Phase 5a deferred-stub error.

### Streaming
The executor reads the file chunk-by-chunk with `Path.open("rb")` so a
1 GB attachment doesn't load into memory or bloat the audit log.
`args["file_path"]` is recorded; `content_bytes_b64` is omitted for the
large path.

### Spec parity
This closes the last open item from spec §19. m365ctl 1.5.0 covers the
full spec surface (Phases 0-14, with the documented "out of scope"
items deferred or noted in CHANGELOG).
```

- [ ] README Mail bullet:
```markdown
- **Chunked attachments (Phase 5a-2, 1.5):** `mail attach add <msg>
  --file <≥3MB-file> --confirm` streams via Graph's upload-session
  protocol. 4 MB chunks, no in-memory buffering.
```

- [ ] `uv sync --all-extras`. Quality gates. Two release commits.

### Task 3.2: Push, PR, merge, tag v1.5.0

Standard cadence.

---

## Self-review

**Spec coverage:**
- ✅ Phase 5a-2 closing item: chunked upload for ≥3 MB attachments.
- ✅ Streams chunks; doesn't load whole file or write base64 to audit log.
- ✅ Replaces the existing "deferred to 5a-2" stub in `mail/cli/attach.py`.

**Type consistency:** `AttachResult` shape unchanged. Audit API matches Phase 6/8/9/12/13. `put_chunk` signature mirrors the rest of `GraphClient`.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-25-phase-5a-2-chunked-upload.md`. Branch `phase-5a-2-chunked-upload` already off `main`.
