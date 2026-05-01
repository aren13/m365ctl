# Graph `$batch` support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt Microsoft Graph `$batch` (≤20 sub-requests per call) across `m365ctl` to collapse per-op round-trips on bulk plan execution, pre-mutation lookups, and read-side fan-out.

**Architecture:** A new `BatchSession` (in `common/batch.py`) buffers Graph calls and flushes them as `/$batch` envelopes, exposing each call as a `BatchFuture`. Mutate verbs split into `start_<verb>` / `finish_<verb>` halves so a `for op in ops` loop can buffer-then-resolve. An `EagerSession` adapter wraps `GraphClient` so verbs work uniformly against both batched and non-batched callers; `GraphClient`'s public surface is unchanged. A new `execute_plan_in_batches` helper drives the two-phase (before-state batch → mutation batch) flow.

**Tech Stack:** Python 3.11+, httpx (sync), pytest with `httpx.MockTransport`, existing `m365ctl.common.retry.with_retry`, existing `m365ctl.common.audit` audit-log primitives.

**Spec:** `docs/superpowers/specs/2026-05-01-graph-batch-support-design.md`

---

## Phase 1 — Foundation

### Task 1: Module skeleton — `BatchUnflushedError`, `BatchFuture`, `_Resolved`, `GraphCaller`

**Files:**
- Create: `src/m365ctl/common/batch.py` (also hosts the `GraphCaller` Protocol — co-located with its two implementations)
- Test: `tests/test_batch.py`

- [ ] **Step 1: Write the failing test for `BatchFuture` resolution states**

```python
# tests/test_batch.py
from __future__ import annotations

import pytest

from m365ctl.common.batch import (
    BatchFuture,
    BatchUnflushedError,
    _Resolved,
)
from m365ctl.common.graph import GraphError


def test_batch_future_unflushed_raises():
    f = BatchFuture(req_id="1")
    with pytest.raises(BatchUnflushedError):
        f.result()
    assert f.done() is False


def test_batch_future_resolves_with_body():
    f = BatchFuture(req_id="1")
    f._resolve(status=200, headers={}, body={"id": "m1"})
    assert f.done() is True
    assert f.result() == {"id": "m1"}
    assert f.status() == 200


def test_batch_future_resolves_with_error():
    f = BatchFuture(req_id="1")
    err = GraphError("ItemNotFound: gone")
    f._resolve_error(err)
    assert f.done() is True
    with pytest.raises(GraphError, match="ItemNotFound"):
        f.result()


def test_resolved_eager_returns_value():
    r = _Resolved(value={"ok": True})
    assert r.result() == {"ok": True}
    assert r.done() is True


def test_resolved_eager_raises_error():
    err = GraphError("BadRequest")
    r = _Resolved(error=err)
    with pytest.raises(GraphError):
        r.result()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_batch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'm365ctl.common.batch'`.

- [ ] **Step 3: Implement the skeleton in `src/m365ctl/common/batch.py`**

```python
"""Microsoft Graph $batch support for m365ctl.

Two key types:

- ``BatchSession`` — buffers Graph calls into ``/$batch`` envelopes of up to
  20 sub-requests, flushes on auto-flush (20th call) or on ``with`` exit.
- ``BatchFuture`` — handle for a sub-response. ``.result()`` raises
  ``BatchUnflushedError`` until the owning session has flushed; thereafter
  returns the parsed body dict (2xx) or raises ``GraphError`` (4xx/5xx).

A thin adapter ``EagerSession`` wraps ``GraphClient`` so verbs can target a
single ``GraphCaller`` protocol regardless of batched vs. eager execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from m365ctl.common.graph import GraphError


class BatchUnflushedError(RuntimeError):
    """Raised by ``BatchFuture.result()`` before the owning session flushes."""


class BatchFuture:
    """Lazy handle for a Graph $batch sub-response.

    Lifecycle:
    1. Constructed by ``BatchSession`` when a call is buffered.
    2. ``.result()`` raises ``BatchUnflushedError`` until ``_resolve`` /
       ``_resolve_error`` is called.
    3. After resolution, ``.result()`` returns the parsed body dict (2xx)
       or raises ``GraphError`` (4xx/5xx, with ``retry_after_seconds`` from
       the sub-response's own ``Retry-After`` header).
    """

    __slots__ = ("_req_id", "_done", "_status", "_headers", "_body", "_error")

    def __init__(self, req_id: str) -> None:
        self._req_id = req_id
        self._done = False
        self._status: int | None = None
        self._headers: dict[str, str] = {}
        self._body: dict | None = None
        self._error: GraphError | None = None

    def done(self) -> bool:
        return self._done

    def result(self) -> dict:
        if not self._done:
            raise BatchUnflushedError(
                f"BatchFuture(req_id={self._req_id!r}) not yet flushed; "
                f"call .result() only after the owning BatchSession has flushed "
                f"(typically on `with` exit)"
            )
        if self._error is not None:
            raise self._error
        return self._body or {}

    def status(self) -> int:
        if not self._done:
            raise BatchUnflushedError(f"BatchFuture(req_id={self._req_id!r}) not flushed")
        assert self._status is not None
        return self._status

    def headers(self) -> dict[str, str]:
        if not self._done:
            raise BatchUnflushedError(f"BatchFuture(req_id={self._req_id!r}) not flushed")
        return dict(self._headers)

    # Internal — called by BatchSession after flush.
    def _resolve(self, *, status: int, headers: dict[str, str], body: dict | None) -> None:
        self._status = status
        self._headers = headers
        self._body = body
        self._done = True

    def _resolve_error(self, error: GraphError) -> None:
        self._error = error
        self._done = True


@dataclass
class _Resolved:
    """Already-resolved future used by EagerSession (synchronous fallback)."""
    value: dict | None = None
    error: GraphError | None = None

    def result(self) -> dict:
        if self.error is not None:
            raise self.error
        return self.value or {}

    def done(self) -> bool:
        return True


class GraphCaller(Protocol):
    """Protocol satisfied by both ``BatchSession`` and ``EagerSession``.

    Verbs (`start_<verb>` / `finish_<verb>`) declare this type so they can
    be driven by either. The returned object always has a ``.result()`` that
    yields a parsed dict body or raises ``GraphError``.
    """
    def get(self, path: str, *, headers: dict | None = None): ...
    def get_absolute(self, url: str, *, headers: dict | None = None): ...
    def post(self, path: str, *, json: dict, headers: dict | None = None): ...
    def patch(self, path: str, *, json_body: dict, headers: dict | None = None): ...
    def delete(self, path: str): ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_batch.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/common/batch.py tests/test_batch.py
git commit -m "feat(batch): scaffold BatchFuture, _Resolved, GraphCaller protocol"
```

---

### Task 2: `BatchSession` buffer + flush envelope + URL normalization + auth strip

**Files:**
- Modify: `src/m365ctl/common/batch.py`
- Test: `tests/test_batch.py` (extend)

- [ ] **Step 1: Write failing tests for buffering, flush envelope, auto-flush at 20, with-exit flush**

```python
# tests/test_batch.py — additions
import json

import httpx

from m365ctl.common.batch import BatchSession
from m365ctl.common.graph import GraphClient


def _stub_graph(handler) -> GraphClient:
    return GraphClient(
        token_provider=lambda: "tok",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )


def test_batch_session_buffers_until_with_exit():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        body = request.read().decode()
        # Echo two successful sub-responses.
        return httpx.Response(200, json={
            "responses": [
                {"id": "1", "status": 200, "headers": {}, "body": {"id": "m1"}},
                {"id": "2", "status": 200, "headers": {}, "body": {"id": "m2"}},
            ],
        })

    graph = _stub_graph(handler)
    with graph.batch() as b:
        f1 = b.get("/me/messages/m1")
        f2 = b.get("/me/messages/m2")
        # Inside `with`, session has not flushed — futures unresolved.
        assert f1.done() is False
        assert f2.done() is False
        # And exactly zero requests have hit the transport.
        assert len(captured) == 0
    # After `with` exits, exactly one /$batch POST has fired and futures resolved.
    assert len(captured) == 1
    req = captured[0]
    assert req.url.path.endswith("/$batch")
    assert req.method == "POST"
    payload = json.loads(req.read())
    assert len(payload["requests"]) == 2
    assert payload["requests"][0] == {
        "id": "1", "method": "GET", "url": "me/messages/m1",
    }
    assert f1.result() == {"id": "m1"}
    assert f2.result() == {"id": "m2"}


def test_batch_session_auto_flush_at_20():
    flushes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        n = len(payload["requests"])
        flushes.append(n)
        return httpx.Response(200, json={
            "responses": [
                {"id": r["id"], "status": 200, "headers": {}, "body": {}} for r in payload["requests"]
            ],
        })

    graph = _stub_graph(handler)
    futures = []
    with graph.batch() as b:
        for i in range(25):
            futures.append(b.get(f"/me/messages/m{i}"))
    # Auto-flush at 20, then with-exit flush of the remaining 5.
    assert flushes == [20, 5]
    assert all(f.done() for f in futures)


def test_batch_session_normalizes_url_and_strips_authorization():
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        captured.append(payload)
        return httpx.Response(200, json={
            "responses": [{"id": "1", "status": 204, "headers": {}}],
        })

    graph = _stub_graph(handler)
    with graph.batch() as b:
        # Both leading-slash and bare path styles must be accepted; sub-request
        # url must be emitted without the leading slash. Authorization header
        # must NOT appear in the sub-request.
        b.delete("/me/messages/m1", headers={"Authorization": "Bearer LEAK", "If-Match": "etag-1"})

    assert captured[0]["requests"][0]["url"] == "me/messages/m1"
    sub_headers = captured[0]["requests"][0].get("headers", {})
    assert "Authorization" not in sub_headers
    assert "authorization" not in {k.lower(): v for k, v in sub_headers.items()}
    assert sub_headers.get("If-Match") == "etag-1"


def test_batch_session_handles_204_no_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "responses": [
                {"id": "1", "status": 204, "headers": {}},  # no body key
            ],
        })

    graph = _stub_graph(handler)
    with graph.batch() as b:
        f = b.delete("/me/messages/m1")
    assert f.result() == {}
    assert f.status() == 204


def test_batch_session_propagates_sub_error_as_graph_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "responses": [
                {"id": "1", "status": 404, "headers": {},
                 "body": {"error": {"code": "ItemNotFound", "message": "gone"}}},
            ],
        })

    graph = _stub_graph(handler)
    with graph.batch() as b:
        f = b.get("/me/messages/missing")
    with pytest.raises(GraphError, match="ItemNotFound"):
        f.result()


def test_batch_session_resolves_responses_keyed_by_id_not_order():
    # Graph spec: $batch may return responses out of order.
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        responses = [
            {"id": r["id"], "status": 200, "headers": {}, "body": {"echo": r["id"]}}
            for r in payload["requests"]
        ]
        responses.reverse()
        return httpx.Response(200, json={"responses": responses})

    graph = _stub_graph(handler)
    with graph.batch() as b:
        f1 = b.get("/me/messages/a")
        f2 = b.get("/me/messages/b")
        f3 = b.get("/me/messages/c")
    assert f1.result() == {"echo": "1"}
    assert f2.result() == {"echo": "2"}
    assert f3.result() == {"echo": "3"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_batch.py -v -k "session"`
Expected: FAIL — `BatchSession` not defined.

- [ ] **Step 3: Implement `BatchSession` (no retry yet — that's Task 3)**

Append to `src/m365ctl/common/batch.py`:

```python
import json as _json
from typing import Callable

from m365ctl.common.graph import GraphClient, _parse_retry_after


_BATCH_MAX = 20


def _normalize_path(path: str) -> str:
    """Strip a leading slash. Graph $batch sub-requests want bare paths."""
    return path[1:] if path.startswith("/") else path


def _strip_auth(headers: dict[str, str] | None) -> dict[str, str]:
    """Remove any Authorization variant; the outer /$batch POST owns auth."""
    if not headers:
        return {}
    return {k: v for k, v in headers.items() if k.lower() != "authorization"}


def _build_subrequest(req_id: str, method: str, path: str,
                      body: dict | None = None,
                      headers: dict[str, str] | None = None) -> dict:
    sub = {"id": req_id, "method": method, "url": _normalize_path(path)}
    clean_headers = _strip_auth(headers)
    if body is not None:
        sub["body"] = body
        clean_headers.setdefault("Content-Type", "application/json")
    if clean_headers:
        sub["headers"] = clean_headers
    return sub


class BatchSession:
    """Buffers Graph calls into /$batch envelopes (≤20 sub-requests each).

    Use as a context manager:

        with graph.batch() as b:
            f = b.get("/me/messages/m1")
            ...
        # `with` exit flushed; f.result() now safe.

    Auto-flush fires when the 20th call is buffered. ``.result()`` on a
    future before its session has flushed raises ``BatchUnflushedError``.
    """

    def __init__(self, graph: GraphClient) -> None:
        self._graph = graph
        self._pending: list[tuple[BatchFuture, dict]] = []
        self._next_id = 0
        self._closed = False

    def __enter__(self) -> "BatchSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Always flush remaining buffered calls, even on exception, so
        # futures don't dangle in an unflushed state forever.
        try:
            if self._pending:
                self._flush_now()
        finally:
            self._closed = True

    def _new_id(self) -> str:
        self._next_id += 1
        return str(self._next_id)

    def _enqueue(self, method: str, path: str, *, body: dict | None,
                 headers: dict | None) -> BatchFuture:
        if self._closed:
            raise RuntimeError("BatchSession is closed")
        req_id = self._new_id()
        sub = _build_subrequest(req_id, method, path, body=body, headers=headers)
        f = BatchFuture(req_id=req_id)
        self._pending.append((f, sub))
        if len(self._pending) >= _BATCH_MAX:
            self._flush_now()
        return f

    def get(self, path: str, *, headers: dict | None = None) -> BatchFuture:
        return self._enqueue("GET", path, body=None, headers=headers)

    def get_absolute(self, url: str, *, headers: dict | None = None) -> BatchFuture:
        # For absolute URLs we still need them to be relative to the Graph
        # base. ``@odata.nextLink`` includes the host; strip it.
        path = url
        for prefix in ("https://graph.microsoft.com/v1.0", "https://graph.microsoft.com/beta"):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break
        return self._enqueue("GET", path, body=None, headers=headers)

    def post(self, path: str, *, json: dict, headers: dict | None = None) -> BatchFuture:
        return self._enqueue("POST", path, body=json, headers=headers)

    def patch(self, path: str, *, json_body: dict, headers: dict | None = None) -> BatchFuture:
        return self._enqueue("PATCH", path, body=json_body, headers=headers)

    def delete(self, path: str, *, headers: dict | None = None) -> BatchFuture:
        return self._enqueue("DELETE", path, body=None, headers=headers)

    def flush(self) -> None:
        """Explicit flush; usually the `with` exit handles this."""
        if self._pending:
            self._flush_now()

    # Internal — actual /$batch dispatch.
    def _flush_now(self) -> None:
        batch = self._pending
        self._pending = []
        self._dispatch(batch)

    def _dispatch(self, batch: list[tuple[BatchFuture, dict]]) -> None:
        envelope = {"requests": [sub for _f, sub in batch]}
        # Use the parent GraphClient's existing post() which already wraps
        # the call in with_retry() for envelope-level transient handling.
        # ``/v1.0`` is the GraphClient base; the literal path "/$batch"
        # appends correctly.
        body = self._graph.post("/$batch", json=envelope)
        self._resolve_responses(batch, body)

    def _resolve_responses(
        self, batch: list[tuple[BatchFuture, dict]], body: dict,
    ) -> None:
        by_id = {f._req_id: f for f, _sub in batch}
        for resp in body.get("responses", []):
            req_id = str(resp.get("id"))
            f = by_id.get(req_id)
            if f is None:
                continue
            status = int(resp.get("status", 0))
            headers = {k: str(v) for k, v in (resp.get("headers") or {}).items()}
            sub_body = resp.get("body")
            if status >= 400:
                err = self._graph_error_from_subresponse(status, sub_body, headers)
                f._resolve_error(err)
            else:
                f._resolve(status=status, headers=headers, body=sub_body if isinstance(sub_body, dict) else None)
        # Any future not represented in responses: resolve as error.
        for f, sub in batch:
            if not f.done():
                f._resolve_error(GraphError(
                    f"HTTP{0}: missing sub-response for id={f._req_id}",
                ))

    def _graph_error_from_subresponse(
        self, status: int, body, headers: dict[str, str],
    ) -> GraphError:
        err = (body.get("error") if isinstance(body, dict) else None) or {}
        code = err.get("code", f"HTTP{status}")
        msg = err.get("message", "")
        return GraphError(
            f"{code}: {msg}",
            retry_after_seconds=_parse_retry_after(headers.get("Retry-After")),
        )
```

Then add at the bottom of `src/m365ctl/common/graph.py`:

```python
def _GraphClient_batch(self):  # placeholder; real method added in Task 4
    raise NotImplementedError("install Task 4 first")
```

…actually, defer the `GraphClient.batch()` method to Task 4. For tests to work in this Task, expose a temporary helper:

```python
# In tests/test_batch.py top:
def _attach_batch(graph):
    """Until Task 4 lands, callers construct BatchSession explicitly."""
    return BatchSession(graph)
```

…but since we want `with graph.batch() as b:` to work in the tests above, just add `GraphClient.batch()` here (Task 4 will reuse it):

In `src/m365ctl/common/graph.py`, inside the `GraphClient` class:

```python
def batch(self) -> "BatchSession":
    """Return a new BatchSession bound to this GraphClient."""
    from m365ctl.common.batch import BatchSession
    return BatchSession(self)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_batch.py -v`
Expected: all PASS (skeleton tests from Task 1 plus session tests from Task 2).

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest -x -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/m365ctl/common/batch.py src/m365ctl/common/graph.py tests/test_batch.py
git commit -m "feat(batch): BatchSession with buffer/flush/auto-flush, URL normalization, auth strip"
```

---

### Task 3: Per-sub transient retry inside `BatchSession.flush`

**Files:**
- Modify: `src/m365ctl/common/batch.py` (`BatchSession._dispatch`)
- Test: `tests/test_batch.py` (extend)

- [ ] **Step 1: Write failing tests for per-sub transient retry**

```python
# tests/test_batch.py — additions
def test_batch_per_sub_429_retried_with_retry_after():
    sleeps: list[float] = []
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        payload = json.loads(request.read())
        ids = [r["id"] for r in payload["requests"]]
        if call_count["n"] == 1:
            # First call: id=1 succeeds, id=2 returns 429 with Retry-After 3.
            return httpx.Response(200, json={
                "responses": [
                    {"id": "1", "status": 200, "headers": {}, "body": {"ok": "first"}},
                    {"id": "2", "status": 429, "headers": {"Retry-After": "3"},
                     "body": {"error": {"code": "TooManyRequests", "message": "slow down"}}},
                ],
            })
        # Second call: only id=2 re-issued, succeeds.
        assert ids == ["2"]
        return httpx.Response(200, json={
            "responses": [
                {"id": "2", "status": 200, "headers": {}, "body": {"ok": "second"}},
            ],
        })

    graph = GraphClient(
        token_provider=lambda: "tok",
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        max_attempts=3,
    )
    with graph.batch() as b:
        f1 = b.get("/me/messages/a")
        f2 = b.get("/me/messages/b")
    assert f1.result() == {"ok": "first"}
    assert f2.result() == {"ok": "second"}
    assert sleeps == [3.0]


def test_batch_per_sub_retry_exhaustion_resolves_with_error():
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "responses": [
                {"id": "1", "status": 503, "headers": {"Retry-After": "1"},
                 "body": {"error": {"code": "serviceNotAvailable", "message": "down"}}},
            ],
        })

    graph = GraphClient(
        token_provider=lambda: "tok",
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        max_attempts=3,
    )
    with graph.batch() as b:
        f = b.get("/me/messages/x")
    with pytest.raises(GraphError, match="serviceNotAvailable"):
        f.result()
    # 3 attempts → 2 sleeps between them.
    assert sleeps == [1.0, 1.0]


def test_batch_permanent_error_not_retried():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={
            "responses": [
                {"id": "1", "status": 404, "headers": {},
                 "body": {"error": {"code": "ItemNotFound", "message": "gone"}}},
            ],
        })

    graph = GraphClient(
        token_provider=lambda: "tok",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        max_attempts=3,
    )
    with graph.batch() as b:
        f = b.get("/me/messages/missing")
    with pytest.raises(GraphError, match="ItemNotFound"):
        f.result()
    assert call_count["n"] == 1   # not retried
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_batch.py -v -k "retry or permanent"`
Expected: FAIL — current `_dispatch` does no per-sub retry.

- [ ] **Step 3: Implement per-sub transient retry**

Replace `_dispatch` in `src/m365ctl/common/batch.py`:

```python
from m365ctl.common.graph import _TRANSIENT_CODES


def _is_transient_status(status: int, code: str) -> bool:
    if status in (429, 500, 502, 503, 504):
        return True
    return code in _TRANSIENT_CODES


class BatchSession:
    # ... existing body ...

    def _dispatch(self, batch: list[tuple[BatchFuture, dict]]) -> None:
        attempts_remaining = self._graph._max_attempts
        pending = list(batch)   # [(future, subrequest_dict), ...]

        while pending:
            envelope = {"requests": [sub for _f, sub in pending]}
            body = self._graph.post("/$batch", json=envelope)
            still_pending: list[tuple[BatchFuture, dict]] = []
            max_retry_after = 0.0

            by_id = {f._req_id: (f, sub) for f, sub in pending}
            for resp in body.get("responses", []):
                req_id = str(resp.get("id"))
                fsub = by_id.get(req_id)
                if fsub is None:
                    continue
                f, sub = fsub
                status = int(resp.get("status", 0))
                headers = {k: str(v) for k, v in (resp.get("headers") or {}).items()}
                sub_body = resp.get("body")
                if status < 400:
                    f._resolve(
                        status=status, headers=headers,
                        body=sub_body if isinstance(sub_body, dict) else None,
                    )
                    continue
                err = self._graph_error_from_subresponse(status, sub_body, headers)
                code = str(err).split(":", 1)[0].strip()
                if attempts_remaining > 1 and _is_transient_status(status, code):
                    still_pending.append((f, sub))
                    if err.retry_after_seconds is not None:
                        max_retry_after = max(max_retry_after, err.retry_after_seconds)
                else:
                    f._resolve_error(err)

            # Any future not in the response array — resolve as error.
            seen_ids = {str(r.get("id")) for r in body.get("responses", [])}
            for f, sub in pending:
                if f._req_id not in seen_ids and not f.done():
                    f._resolve_error(GraphError(
                        f"HTTP0: missing sub-response for id={f._req_id}",
                    ))

            if not still_pending:
                return
            attempts_remaining -= 1
            if attempts_remaining <= 0:
                # Exhausted — resolve the still-pending with their last error.
                # Re-issue one final time? No — we've used max_attempts.
                # The error from the last attempt was already captured; resolve them.
                for f, _sub in still_pending:
                    if not f.done():
                        f._resolve_error(GraphError(
                            "HTTP429: retry exhausted",
                        ))
                return
            # Sleep before retrying just the transient sub-requests.
            delay = max_retry_after if max_retry_after > 0 else 1.0
            self._graph._sleep(delay)
            pending = still_pending
```

Wait — the exhaustion path above doesn't use the *last* GraphError. Adjust: capture last err per future and resolve with it on exhaustion. Replace the loop bookkeeping to track `last_err_by_id` and resolve still-pending with it on exhaustion:

```python
    def _dispatch(self, batch: list[tuple[BatchFuture, dict]]) -> None:
        attempts_remaining = self._graph._max_attempts
        pending = list(batch)
        last_err_by_id: dict[str, GraphError] = {}

        while pending:
            envelope = {"requests": [sub for _f, sub in pending]}
            body = self._graph.post("/$batch", json=envelope)
            still_pending: list[tuple[BatchFuture, dict]] = []
            max_retry_after = 0.0

            seen_ids: set[str] = set()
            for resp in body.get("responses", []):
                req_id = str(resp.get("id"))
                seen_ids.add(req_id)
                f = next((f for f, _ in pending if f._req_id == req_id), None)
                sub = next((sub for fp, sub in pending if fp._req_id == req_id), None)
                if f is None:
                    continue
                status = int(resp.get("status", 0))
                headers = {k: str(v) for k, v in (resp.get("headers") or {}).items()}
                sub_body = resp.get("body")
                if status < 400:
                    f._resolve(
                        status=status, headers=headers,
                        body=sub_body if isinstance(sub_body, dict) else None,
                    )
                    continue
                err = self._graph_error_from_subresponse(status, sub_body, headers)
                last_err_by_id[req_id] = err
                code = str(err).split(":", 1)[0].strip()
                if attempts_remaining > 1 and _is_transient_status(status, code):
                    still_pending.append((f, sub))
                    if err.retry_after_seconds is not None:
                        max_retry_after = max(max_retry_after, err.retry_after_seconds)
                else:
                    f._resolve_error(err)

            for f, _sub in pending:
                if f._req_id not in seen_ids and not f.done():
                    f._resolve_error(GraphError(
                        f"HTTP0: missing sub-response for id={f._req_id}",
                    ))

            if not still_pending:
                return
            attempts_remaining -= 1
            if attempts_remaining <= 0:
                for f, _sub in still_pending:
                    if not f.done():
                        f._resolve_error(
                            last_err_by_id.get(f._req_id, GraphError("HTTP429: retry exhausted"))
                        )
                return
            delay = max_retry_after if max_retry_after > 0 else 1.0
            self._graph._sleep(delay)
            pending = still_pending
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_batch.py -v`
Expected: all PASS.

- [ ] **Step 5: Run full suite**

Run: `uv run pytest -x -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/m365ctl/common/batch.py tests/test_batch.py
git commit -m "feat(batch): per-sub transient retry honoring sub-response Retry-After"
```

---

### Task 4: `EagerSession` adapter

**Files:**
- Modify: `src/m365ctl/common/batch.py`
- Test: `tests/test_batch.py` (extend)

- [ ] **Step 1: Write failing tests for `EagerSession`**

```python
# tests/test_batch.py — additions
from m365ctl.common.batch import EagerSession


def test_eager_session_wraps_success():
    def handler(request):
        return httpx.Response(200, json={"id": "m1"})

    graph = GraphClient(
        token_provider=lambda: "tok", transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )
    s = EagerSession(graph)
    f = s.get("/me/messages/m1")
    assert f.done() is True
    assert f.result() == {"id": "m1"}


def test_eager_session_wraps_error():
    def handler(request):
        return httpx.Response(404, json={"error": {"code": "ItemNotFound", "message": "gone"}})

    graph = GraphClient(
        token_provider=lambda: "tok", transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )
    s = EagerSession(graph)
    f = s.get("/me/messages/missing")
    assert f.done() is True
    with pytest.raises(GraphError, match="ItemNotFound"):
        f.result()


def test_eager_session_post_passes_through_json_body():
    captured: list[bytes] = []

    def handler(request):
        captured.append(request.read())
        return httpx.Response(200, json={"ok": True})

    graph = GraphClient(
        token_provider=lambda: "tok", transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )
    s = EagerSession(graph)
    f = s.post("/me/messages/m1/move", json={"destinationId": "archive"})
    assert f.result() == {"ok": True}
    assert b"destinationId" in captured[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_batch.py -v -k "eager"`
Expected: FAIL — `EagerSession` not defined.

- [ ] **Step 3: Implement `EagerSession`**

Append to `src/m365ctl/common/batch.py`:

```python
class EagerSession:
    """GraphCaller adapter wrapping a GraphClient for synchronous execution.

    Lets verb code (`start_<verb>`, `finish_<verb>`) target a single
    GraphCaller protocol regardless of batched vs. eager use. Each call
    issues immediately and the returned ``_Resolved`` is already done; HTTP
    errors surface from ``.result()``, not from the call itself.
    """

    def __init__(self, graph: GraphClient) -> None:
        self._g = graph

    def get(self, path: str, *, headers: dict | None = None) -> _Resolved:
        try:
            return _Resolved(value=self._g.get(path, headers=headers))
        except GraphError as e:
            return _Resolved(error=e)

    def get_absolute(self, url: str, *, headers: dict | None = None) -> _Resolved:
        try:
            return _Resolved(value=self._g.get_absolute(url, headers=headers))
        except GraphError as e:
            return _Resolved(error=e)

    def post(self, path: str, *, json: dict, headers: dict | None = None) -> _Resolved:
        try:
            return _Resolved(value=self._g.post(path, json=json, headers=headers))
        except GraphError as e:
            return _Resolved(error=e)

    def patch(self, path: str, *, json_body: dict, headers: dict | None = None) -> _Resolved:
        try:
            return _Resolved(value=self._g.patch(path, json_body=json_body, headers=headers))
        except GraphError as e:
            return _Resolved(error=e)

    def delete(self, path: str, *, headers: dict | None = None) -> _Resolved:
        try:
            self._g.delete(path)
            return _Resolved(value={})
        except GraphError as e:
            return _Resolved(error=e)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_batch.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/common/batch.py tests/test_batch.py
git commit -m "feat(batch): EagerSession adapter unifying batched + sync GraphCaller"
```

---

## Phase 2 — Bulk plan executor

### Task 5: `execute_plan_in_batches` helper (mail) + OneDrive sibling

**Files:**
- Modify: `src/m365ctl/mail/cli/_bulk.py`
- Modify: `src/m365ctl/onedrive/cli/_common.py`
- Test: `tests/test_mail_cli_bulk.py` (extend)

- [ ] **Step 1: Write failing test for two-phase execution**

```python
# tests/test_mail_cli_bulk.py — additions
import json
from unittest.mock import MagicMock

import httpx

from m365ctl.common.audit import AuditLogger
from m365ctl.common.batch import BatchFuture
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation
from m365ctl.mail.cli._bulk import execute_plan_in_batches
from m365ctl.mail.mutate._common import MailResult


def _op(op_id: str) -> Operation:
    return Operation(op_id=op_id, action="mail.move", drive_id="me", item_id=op_id,
                     args={"destination_id": "archive"})


def test_execute_plan_in_batches_runs_phase1_then_phase2(tmp_path):
    """Use a real GraphClient with MockTransport so /$batch hits a stub."""
    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        posts.append(body)
        # Echo: every sub-request returns 200 with an empty body.
        return httpx.Response(200, json={
            "responses": [
                {"id": r["id"], "status": 200, "headers": {}, "body": {"id": "echo-" + r["id"]}}
                for r in body["requests"]
            ],
        })

    graph = GraphClient(
        token_provider=lambda: "tok",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    ops = [_op(f"op{i}") for i in range(3)]

    def fetch_before(b, op):
        return b.get(f"/me/messages/{op.item_id}")

    def parse_before(op, body, err):
        return {"parent_folder_id": "inbox"} if body else {}

    def start_op(op, b, logger, *, before):
        f = b.post(f"/me/messages/{op.item_id}/move", json={"destinationId": "archive"})
        return f, {"parent_folder_id": "archive"}

    def finish_op(op, future, after, logger):
        try:
            future.result()
        except Exception as e:
            return MailResult(op_id=op.op_id, status="error", error=str(e))
        return MailResult(op_id=op.op_id, status="ok", after=after)

    results: list[tuple[Operation, MailResult]] = []
    rc = execute_plan_in_batches(
        graph=graph, logger=logger, ops=ops,
        fetch_before=fetch_before, parse_before=parse_before,
        start_op=start_op, finish_op=finish_op,
        on_result=lambda op, r: results.append((op, r)),
    )
    assert rc == 0
    # Two /$batch POSTs: phase 1 (3 GETs), phase 2 (3 POSTs).
    assert len(posts) == 2
    assert all(r["method"] == "GET" for r in posts[0]["requests"])
    assert all(r["method"] == "POST" for r in posts[1]["requests"])
    assert [r.status for _, r in results] == ["ok", "ok", "ok"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mail_cli_bulk.py::test_execute_plan_in_batches_runs_phase1_then_phase2 -v`
Expected: FAIL — `execute_plan_in_batches` not defined.

- [ ] **Step 3: Implement `execute_plan_in_batches` in `mail/cli/_bulk.py`**

Add to the existing `src/m365ctl/mail/cli/_bulk.py`:

```python
from typing import Any, Callable, Optional

from m365ctl.common.audit import AuditLogger
from m365ctl.common.batch import BatchFuture
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate._common import MailResult


def execute_plan_in_batches(
    *,
    graph: GraphClient,
    logger: AuditLogger,
    ops: list[Operation],
    fetch_before: Optional[Callable[[Any, Operation], BatchFuture]],
    parse_before: Callable[[Operation, dict | None, GraphError | None], dict],
    start_op: Callable[..., tuple[BatchFuture, dict]],
    finish_op: Callable[..., MailResult],
    on_result: Callable[[Operation, MailResult], None],
) -> int:
    """Two-phase batched plan execution.

    Phase 1: batch all `before` GETs (skipped if ``fetch_before`` is None).
    Phase 2: buffer all mutations under one BatchSession; ``with`` exit
    flushes them. Then resolve futures via ``finish_op`` for each.

    Returns 1 if any op errored, else 0.
    """
    # Phase 1.
    befores: dict[str, dict] = {}
    if fetch_before is not None:
        with graph.batch() as b:
            phase1 = [(op, fetch_before(b, op)) for op in ops]
        for op, f in phase1:
            try:
                befores[op.op_id] = parse_before(op, f.result(), None)
            except GraphError as e:
                befores[op.op_id] = parse_before(op, None, e)

    # Phase 2.
    with graph.batch() as b:
        pending = [
            (op, *start_op(op, b, logger, before=befores.get(op.op_id, {})))
            for op in ops
        ]

    any_error = False
    for op, future, after in pending:
        result = finish_op(op, future, after, logger)
        on_result(op, result)
        if result.status != "ok":
            any_error = True
    return 1 if any_error else 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mail_cli_bulk.py::test_execute_plan_in_batches_runs_phase1_then_phase2 -v`
Expected: PASS.

- [ ] **Step 5: Add OneDrive sibling**

In `src/m365ctl/onedrive/cli/_common.py`, add a re-export so OneDrive callers don't reach into mail:

```python
# Shared with mail; OneDrive results use a different dataclass but the
# helper is generic over the result type.
from m365ctl.mail.cli._bulk import execute_plan_in_batches  # re-export

__all__ = [..., "execute_plan_in_batches"]
```

- [ ] **Step 6: Run full suite**

Run: `uv run pytest -x -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/m365ctl/mail/cli/_bulk.py src/m365ctl/onedrive/cli/_common.py tests/test_mail_cli_bulk.py
git commit -m "feat(batch): two-phase plan executor (mail + onedrive shared)"
```

---

## Phase 3 — Mail mutate verbs (one task per verb)

Each verb in this phase follows the same pattern: split the existing `execute_<verb>` into `start_<verb>` / `finish_<verb>` (both taking a `GraphCaller`) plus a thin `execute_<verb>` shim that wraps a GraphClient in `EagerSession`. Then update the matching `cli/<verb>.py --from-plan` block to call `execute_plan_in_batches` with the new functions.

### Task 6: `mail.move`

**Files:**
- Modify: `src/m365ctl/mail/mutate/move.py`
- Modify: `src/m365ctl/mail/cli/move.py` (`--from-plan` block, lines ~89-122)
- Test: `tests/test_mail_mutate_move.py` (verify existing tests still pass)
- Test: `tests/test_cli_mail_move.py` (add batched-from-plan test)

- [ ] **Step 1: Write failing test asserting `--from-plan` issues batched POSTs**

```python
# tests/test_cli_mail_move.py — addition
import json

import httpx

from m365ctl.common.planfile import Plan, PLAN_SCHEMA_VERSION, Operation, write_plan


def test_mail_move_from_plan_uses_batch(tmp_path, monkeypatch):
    """Bulk move via plan file should issue $batch envelopes, not N individual POSTs."""
    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        posts.append({"path": request.url.path, "body": body})
        return httpx.Response(200, json={
            "responses": [
                {"id": r["id"], "status": 200, "headers": {},
                 "body": {"id": "x", "parentFolderId": "inbox"}}
                for r in body["requests"]
            ],
        })

    # ... write a plan with 5 mail.move ops to tmp_path/plan.json
    # ... invoke `m365ctl mail move --from-plan ... --confirm` via the CLI entry point
    # ... assert that exactly 2 /$batch POSTs were made (1 phase-1 GETs, 1 phase-2 POSTs)
    # (Concrete CLI invocation copied from existing `test_cli_mail_move.py` patterns.)
    assert all(p["path"].endswith("/$batch") for p in posts)
    assert len(posts) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_mail_move.py::test_mail_move_from_plan_uses_batch -v`
Expected: FAIL — current CLI issues N×2 individual GETs/POSTs.

- [ ] **Step 3: Refactor `src/m365ctl/mail/mutate/move.py`**

```python
"""Message move — POST /messages/{id}/move with {destinationId}."""
from __future__ import annotations

from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.batch import BatchFuture, EagerSession, GraphCaller
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.mutate._common import MailResult


def _user_base(op: Operation) -> str:
    auth_mode = op.args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)


def start_move(
    op: Operation,
    client: GraphCaller,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> tuple[BatchFuture, dict[str, Any]]:
    """Log start, buffer the move POST, return (future, after-projection)."""
    dest_id = op.args["destination_id"]
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-move",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    f = client.post(
        f"{ub}/messages/{op.item_id}/move",
        json={"destinationId": dest_id},
    )
    after: dict[str, Any] = {"parent_folder_id": dest_id}
    return f, after


def finish_move(
    op: Operation,
    future,
    after: dict[str, Any],
    logger: AuditLogger,
) -> MailResult:
    """Resolve future, log end, return MailResult."""
    try:
        future.result()
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_move(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """Single-op convenience for non-batched callers."""
    eager = EagerSession(graph)
    f, after = start_move(op, eager, logger, before=before)
    return finish_move(op, f, after, logger)
```

- [ ] **Step 4: Run mutate tests to confirm `execute_move` still works**

Run: `uv run pytest tests/test_mail_mutate_move.py -v`
Expected: all PASS (existing 3 tests).

- [ ] **Step 5: Refactor `--from-plan` block in `src/m365ctl/mail/cli/move.py` (replace lines 89-122)**

```python
    # --- From-plan mode (bulk execute, batched) -----------------------------
    if args.from_plan:
        if not args.confirm:
            print("mail move --from-plan requires --confirm.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        plan = load_plan(Path(args.from_plan))
        ops = [op for op in plan.operations if op.action == "mail.move"]
        if not ops:
            print("mail move --from-plan: no mail.move ops in plan.", file=sys.stderr)
            return 2
        if not confirm_bulk_proceed(len(ops), verb="move"):
            print("aborted: user declined /dev/tty confirm.", file=sys.stderr)
            return 2
        for op in ops:
            op.args.setdefault("auth_mode", auth_mode)
        token = cred.get_token()
        graph = GraphClient(token_provider=lambda: token)
        logger = AuditLogger(ops_dir=cfg.logging.ops_dir)

        def fetch_before(b, op):
            ub = _user_base_for_op(op, args.mailbox, auth_mode)
            return b.get(f"{ub}/messages/{op.item_id}")

        def parse_before(op, body, err):
            if not body:
                return {}
            return {
                "parent_folder_id": body.get("parentFolderId"),
                "parent_folder_path": None,
            }

        def on_result(op, result):
            if result.status == "ok":
                print(f"[{op.op_id}] ok")
            else:
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)

        from m365ctl.mail.mutate.move import start_move, finish_move
        return execute_plan_in_batches(
            graph=graph, logger=logger, ops=ops,
            fetch_before=fetch_before, parse_before=parse_before,
            start_op=start_move, finish_op=finish_move,
            on_result=on_result,
        )
```

Add `_user_base_for_op` helper near the top of the file (mirrors `mutate.move._user_base`):

```python
def _user_base_for_op(op, mailbox_spec, auth_mode):
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)
```

Imports to add: `from m365ctl.mail.cli._bulk import execute_plan_in_batches`.

- [ ] **Step 6: Run all move tests**

Run: `uv run pytest tests/test_cli_mail_move.py tests/test_mail_mutate_move.py -v`
Expected: all PASS, including the new batched test.

- [ ] **Step 7: Commit**

```bash
git add src/m365ctl/mail/mutate/move.py src/m365ctl/mail/cli/move.py tests/test_cli_mail_move.py
git commit -m "refactor(mail): batch mail.move bulk plan execution via $batch"
```

---

### Task 7: `mail.delete`

**Files:**
- Modify: `src/m365ctl/mail/mutate/delete.py`
- Modify: `src/m365ctl/mail/cli/delete.py` (`--from-plan` block)
- Test: `tests/test_mail_mutate_delete.py`, `tests/test_cli_mail_delete.py`

- [ ] **Step 1: Write failing test for batched `--from-plan` (mirror Task 6 Step 1).**

- [ ] **Step 2: Refactor `src/m365ctl/mail/mutate/delete.py`** to expose `start_delete` / `finish_delete` / `execute_delete`.

Pattern: `start_delete` calls `client.delete(f"{ub}/messages/{op.item_id}")` and returns `(future, after={"deleted": True})`. The audit `start` records the soft-delete intent (move to Deleted Items) or hard-delete, depending on `op.args.get("permanent")`. If `permanent`, use `client.post(f"{ub}/messages/{op.item_id}/permanentDelete", json={})` instead.

Concrete code:

```python
def start_delete(op, client, logger, *, before):
    permanent = bool(op.args.get("permanent"))
    ub = _user_base(op)
    log_mutation_start(logger, op_id=op.op_id, cmd="mail-delete",
                      args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before)
    if permanent:
        f = client.post(f"{ub}/messages/{op.item_id}/permanentDelete", json={})
    else:
        f = client.delete(f"{ub}/messages/{op.item_id}")
    after = {"deleted": True, "permanent": permanent}
    return f, after


def finish_delete(op, future, after, logger):
    try:
        future.result()
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_delete(op, graph, logger, *, before):
    eager = EagerSession(graph)
    f, after = start_delete(op, eager, logger, before=before)
    return finish_delete(op, f, after, logger)
```

- [ ] **Step 3: Update `cli/delete.py` `--from-plan` block** mirroring Task 6 Step 5. `fetch_before` does a `b.get(f"{ub}/messages/{op.item_id}?$select=id,parentFolderId")` to capture parent folder for undo. `parse_before` returns `{"parent_folder_id": body["parentFolderId"], "subject": body.get("subject")}` for undo records.

- [ ] **Step 4: Run all delete tests**

Run: `uv run pytest tests/test_mail_mutate_delete.py tests/test_cli_mail_delete.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/mail/mutate/delete.py src/m365ctl/mail/cli/delete.py tests/test_cli_mail_delete.py
git commit -m "refactor(mail): batch mail.delete bulk plan execution"
```

---

### Task 8: `mail.copy`

**Files:**
- Modify: `src/m365ctl/mail/mutate/copy.py`
- Modify: `src/m365ctl/mail/cli/copy.py`
- Test: `tests/test_mail_mutate_copy.py`, `tests/test_cli_mail_copy.py`

Pattern: like `move`, but the underlying call is `POST /messages/{id}/copy {destinationId}`. The Graph response body contains the new message id; `after` records `{"copied_message_id": body["id"], "destination_folder_id": dest}`. Note: in `finish_copy`, if `future.result()` returns a body with `id`, capture it into `after` (mutating the dict in place) before logging the end record.

- [ ] **Step 1: Failing batched-from-plan test (mirror Task 6 Step 1).**
- [ ] **Step 2: Refactor `mutate/copy.py`** with `start_copy` / `finish_copy` / `execute_copy`. Critical: `finish_copy` reads the response body to populate `after["copied_message_id"]`:

```python
def finish_copy(op, future, after, logger):
    try:
        body = future.result()
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after = {**after, "copied_message_id": body.get("id")}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
```

- [ ] **Step 3: Update `cli/copy.py` `--from-plan` block.**
- [ ] **Step 4: Run tests.**
- [ ] **Step 5: Commit.**

```bash
git add src/m365ctl/mail/mutate/copy.py src/m365ctl/mail/cli/copy.py tests/test_cli_mail_copy.py
git commit -m "refactor(mail): batch mail.copy bulk plan execution"
```

---

### Task 9: `mail.categorize`

**Files:** `mail/mutate/categorize.py`, `mail/cli/categorize.py`, `tests/test_mail_mutate_categorize.py`, `tests/test_cli_mail_categorize.py`

Pattern: `PATCH /messages/{id}` with `{"categories": [...]}` body. `start_categorize`:

```python
def start_categorize(op, client, logger, *, before):
    cats = op.args["categories"]  # list[str]
    ub = _user_base(op)
    log_mutation_start(logger, op_id=op.op_id, cmd="mail-categorize",
                      args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before)
    f = client.patch(f"{ub}/messages/{op.item_id}", json_body={"categories": cats})
    return f, {"categories": cats}
```

`fetch_before` issues `b.get(f"{ub}/messages/{op.item_id}?$select=id,categories")`; `parse_before` extracts current categories.

- [ ] Steps 1-5 (test, refactor, CLI, test, commit).

```bash
git commit -m "refactor(mail): batch mail.categorize bulk plan execution"
```

---

### Task 10: `mail.flag`

**Files:** `mail/mutate/flag.py`, `mail/cli/flag.py`, `tests/test_mail_mutate_flag.py`, `tests/test_cli_mail_flag.py`

Pattern: `PATCH /messages/{id}` with `{"flag": {"flagStatus": "flagged"|"complete"|"notFlagged"}}`. `fetch_before=None` is acceptable (the args fully describe the new state) — pass `None` to skip Phase 1 and save round-trips. The audit `before` captures the args directly:

```python
# In cli/flag.py:
return execute_plan_in_batches(
    graph=graph, logger=logger, ops=ops,
    fetch_before=None,
    parse_before=lambda op, body, err: {},   # not called when fetch_before is None
    start_op=start_flag, finish_op=finish_flag,
    on_result=on_result,
)
```

- [ ] Steps 1-5.

```bash
git commit -m "refactor(mail): batch mail.flag bulk plan execution (no phase-1)"
```

---

### Task 11: `mail.read`

**Files:** `mail/mutate/read.py`, `mail/cli/read.py`, `tests/test_mail_mutate_read.py`, `tests/test_cli_mail_read.py`

Pattern: `PATCH /messages/{id}` with `{"isRead": true|false}`. Same `fetch_before=None` shortcut as flag.

- [ ] Steps 1-5.

```bash
git commit -m "refactor(mail): batch mail.read bulk plan execution (no phase-1)"
```

---

### Task 12: `mail.forward`

**Files:** `mail/mutate/forward.py`, `mail/cli/forward.py`, `tests/test_mail_mutate_forward.py`, `tests/test_cli_mail_forward.py`

Pattern: `POST /messages/{id}/forward` with `{toRecipients, comment}`. After-state: `{"forwarded_to": [...]}`. `fetch_before` may capture the original `subject` for audit context — choose based on existing audit shape.

- [ ] Steps 1-5.

```bash
git commit -m "refactor(mail): batch mail.forward bulk plan execution"
```

---

### Task 13: `mail.reply`

**Files:** `mail/mutate/reply.py`, `mail/cli/reply.py`, `tests/test_mail_mutate_reply.py`, `tests/test_cli_mail_reply.py`

Pattern: `POST /messages/{id}/reply` (or `/replyAll`). Same shape as forward.

- [ ] Steps 1-5.

```bash
git commit -m "refactor(mail): batch mail.reply / mail.replyAll bulk plan execution"
```

---

### Task 14: `mail.archive` and `mail.snooze` CLI bulk paths

**Files:** `mail/cli/archive.py`, `mail/cli/snooze.py`, related tests.

Both verbs build `mail.move` ops at runtime (archive = move to Archive folder, snooze = move to a snooze folder + flag). Their `--from-plan` paths invoke the move bulk path under the hood — verify they pick up the batched `execute_plan_in_batches` automatically. If they have their own plan-iteration loop, refactor it to delegate to `execute_plan_in_batches` using `start_move` / `finish_move`.

- [ ] **Step 1: Inspect `cli/archive.py` and `cli/snooze.py` for inline plan loops.** If they call `execute_move` in a loop, refactor to `execute_plan_in_batches`.

- [ ] **Step 2: Write batched-from-plan tests for each.**

- [ ] **Step 3: Refactor.**

- [ ] **Step 4: Tests pass.**

- [ ] **Step 5: Commit.**

```bash
git commit -m "refactor(mail): batch mail.archive and mail.snooze plan execution"
```

---

### Task 15: `mail.focus`

**Files:** `mail/mutate/focus.py`, `mail/cli/focus.py`

Pattern: `PATCH /messages/{id}` with `{"inferenceClassification": "focused"|"other"}`. Same shape as `flag` / `read`.

- [ ] Steps 1-5.

```bash
git commit -m "refactor(mail): batch mail.focus bulk plan execution"
```

---

## Phase 4 — OneDrive mutate verbs (one task per verb)

### Task 16: `onedrive.move`

**Files:** `src/m365ctl/onedrive/mutate/move.py`, `src/m365ctl/onedrive/cli/move.py`, `tests/test_mutate_move.py`, `tests/test_cli_move.py`

Pattern: `PATCH /drives/{drive-id}/items/{item-id}` with `{"parentReference": {"id": dest_id}}` body. Result: split into `start_od_move` / `finish_od_move` / `execute_move` (note: name `execute_move` is the OneDrive one, not mail).

`fetch_before` does `b.get(f"/drives/{drive}/items/{item}?$select=id,parentReference,name")` to capture original parent for undo.

- [ ] **Step 1: Failing batched-from-plan test in `tests/test_cli_move.py`.**
- [ ] **Step 2: Refactor `onedrive/mutate/move.py`** with the start/finish/execute trio.
- [ ] **Step 3: Update `onedrive/cli/move.py` `--from-plan` block** to use `execute_plan_in_batches`. The result type for OneDrive mutate is the existing dataclass (e.g., `MoveResult`); `execute_plan_in_batches`'s callable signatures are generic enough that this works.
- [ ] **Step 4: Tests pass.**
- [ ] **Step 5: Commit.**

```bash
git commit -m "refactor(onedrive): batch onedrive.move bulk plan execution"
```

---

### Task 17: `onedrive.copy`

**Files:** `onedrive/mutate/copy.py`, `onedrive/cli/copy.py`, `tests/test_mutate_copy.py`, `tests/test_cli_copy.py`

Pattern: `POST /drives/{drive-id}/items/{item-id}/copy` returns `202 Accepted` with a `Location` header pointing to a monitor URL. Two implications:

1. The `BatchFuture` for the copy must expose status/headers (we already added `f.status()` / `f.headers()` in Task 1). `finish_copy` reads `f.headers().get("Location")` and stores it in `after["monitor_url"]`.
2. Polling the monitor URL (today's behavior) stays out-of-band for the first cut; record the URL in audit and let undo / status verbs poll later. This preserves current semantics.

- [ ] Steps 1-5.

```bash
git commit -m "refactor(onedrive): batch onedrive.copy bulk plan execution"
```

---

### Task 18: `onedrive.delete`

**Files:** `onedrive/mutate/delete.py`, `onedrive/cli/delete.py`, `tests/test_mutate_delete.py`, `tests/test_cli_delete.py`

Pattern: `DELETE /drives/{drive-id}/items/{item-id}`. Same shape as mail.delete (no body).

- [ ] Steps 1-5.

```bash
git commit -m "refactor(onedrive): batch onedrive.delete bulk plan execution"
```

---

### Task 19: `onedrive.rename`

**Files:** `onedrive/mutate/rename.py`, `onedrive/cli/rename.py`, `tests/test_mutate_rename.py`, `tests/test_cli_rename.py`

Pattern: `PATCH /drives/{drive-id}/items/{item-id}` with `{"name": new_name}`.

- [ ] Steps 1-5.

```bash
git commit -m "refactor(onedrive): batch onedrive.rename bulk plan execution"
```

---

### Task 20: `onedrive.label`

**Files:** `onedrive/mutate/label.py`, `onedrive/cli/label.py`, `tests/test_mutate_label.py`, `tests/test_cli_label.py`

Pattern: `PATCH /drives/{drive-id}/items/{item-id}/extensions/...` (depending on label scheme used). Mechanically identical.

- [ ] Steps 1-5.

```bash
git commit -m "refactor(onedrive): batch onedrive.label bulk plan execution"
```

---

## Phase 5 — Read-side fan-out

Each task in this phase wraps a `for ... in ...:` loop in `with graph.batch() as b:` plus a second resolve pass. No external API change. Test pattern: assert exactly `ceil(N/20)` POSTs to `/$batch` for N inputs, with the expected sub-request URLs.

### Task 21: `mail.messages.list_messages` over folders

**Files:** `src/m365ctl/mail/messages.py`, `src/m365ctl/mail/cli/_bulk.py` (`expand_messages_for_pattern`), `tests/test_mail_messages.py`, `tests/test_mail_cli_bulk.py`

`expand_messages_for_pattern` today calls `list_messages(...)` once per folder serially. Convert the *first-page fetch* of each folder to a single batch:

```python
def expand_messages_for_pattern(*, graph, mailbox_spec, auth_mode, resolved_folders,
                                 filter, limit=50, page_size=50,
                                 _list_messages_impl=_default_list_messages):
    # Phase 1: batch the first-page GET of each folder.
    list_filters = filter.as_list_filters()
    with graph.batch() as b:
        firsts = []
        for folder_id, folder_path in resolved_folders:
            url = _build_messages_url(mailbox_spec, auth_mode, folder_id, list_filters, page_size)
            firsts.append((folder_id, folder_path, b.get(url)))
    # Phase 2: walk pages serially per folder (pagination is sequential by nature).
    yielded = 0
    for folder_id, folder_path, f in firsts:
        try:
            page = f.result()
        except GraphError:
            continue
        for msg_dict in page.get("value", []):
            msg = _parse_message(msg_dict, parent_folder_path=folder_path)
            if filter.match(msg):
                yield msg
                yielded += 1
                if yielded >= limit:
                    return
        next_link = page.get("@odata.nextLink")
        while next_link and yielded < limit:
            page = graph.get_absolute(next_link)
            for msg_dict in page.get("value", []):
                msg = _parse_message(msg_dict, parent_folder_path=folder_path)
                if filter.match(msg):
                    yield msg
                    yielded += 1
                    if yielded >= limit:
                        return
            next_link = page.get("@odata.nextLink")
```

`_build_messages_url` and `_parse_message` are extracted from the existing `list_messages` body so the batched path can mirror the URL construction and parsing.

- [ ] **Step 1: Failing test that asserts one `/$batch` POST is made for `expand_messages_for_pattern` over 3 folders.**
- [ ] **Step 2: Extract `_build_messages_url` / `_parse_message` from `list_messages`.**
- [ ] **Step 3: Implement the new `expand_messages_for_pattern` body.**
- [ ] **Step 4: Tests pass.**
- [ ] **Step 5: Commit.**

```bash
git commit -m "refactor(mail): batch first-page GETs across folders in expand_messages_for_pattern"
```

---

### Task 22: `mail.folders.resolve_folder_path` over a list

**Files:** `src/m365ctl/mail/folders.py`, `tests/test_mail_folders.py`

Add a new public helper `resolve_folder_paths(paths: list[str], graph, *, mailbox_spec, auth_mode) -> dict[str, str]` that batches the per-tier lookups across all paths simultaneously. Existing single-path callers are unchanged.

- [ ] **Step 1: Failing test asserting `resolve_folder_paths(["/A", "/B", "/C"], ...)` issues one `/$batch` per depth tier.**
- [ ] **Step 2: Implement using BatchSession over a per-tier dict of `{path: BatchFuture}` keyed by depth.**
- [ ] **Step 3: Tests pass.**
- [ ] **Step 4: Commit.**

```bash
git commit -m "feat(mail): batched resolve_folder_paths for list inputs"
```

---

### Task 23: `mail/mutate/attach.py` per-message attachment listings

**Files:** `src/m365ctl/mail/mutate/attach.py`, `tests/test_mail_mutate_attach.py`, `tests/test_mail_attachments.py`

Convert the per-message `GET /messages/{id}/attachments` listing pass (used in archive / export flows that work over many messages) to a batched first pass.

- [ ] **Step 1: Failing test on the helper that lists attachments for N messages, expecting ⌈N/20⌉ batched POSTs.**
- [ ] **Step 2: Refactor.**
- [ ] **Step 3: Tests pass.**
- [ ] **Step 4: Commit.**

```bash
git commit -m "refactor(mail): batch per-message attachment listings"
```

---

### Task 24: `mail/cli/export.py` and `mail/export/` body+headers fetch

**Files:** `src/m365ctl/mail/export/*.py`, `src/m365ctl/mail/cli/export.py`, related tests.

The export flow today fetches body + headers for each message serially. Convert the metadata-fetch pass to batched (chunks of 20). Body-bytes for non-large attachments can also be batched — large attachments must continue to use upload sessions and are out of scope.

- [ ] **Step 1: Failing tests for the batched metadata pass.**
- [ ] **Step 2: Refactor; reuse `BatchSession`.**
- [ ] **Step 3: Tests pass.**
- [ ] **Step 4: Commit.**

```bash
git commit -m "refactor(mail): batch per-message metadata fetch in export flow"
```

---

### Task 25: `mail/triage/runner.py` per-message classification metadata

**Files:** `src/m365ctl/mail/triage/runner.py`, `tests/test_triage_runner.py`

The triage runner fetches per-message classification metadata in a loop. Convert to batched chunks of 20.

- [ ] Steps 1-4 (test → refactor → pass → commit).

```bash
git commit -m "refactor(mail): batch per-message metadata fetch in triage runner"
```

---

### Task 26: `mail/catalog/` non-delta fan-out

**Files:** `src/m365ctl/mail/catalog/*.py`, related tests.

Inspect the catalog crawl for non-delta paths that issue per-folder GETs. Convert the first-page-per-folder pass to batched.

Skip any code path that uses `/delta` tokens — those must stay sequential (each page depends on the prior `@odata.deltaLink`).

- [ ] **Step 1: Identify non-delta fan-out spots (grep for `for folder` / `for fid` patterns issuing GETs).**
- [ ] **Step 2: Failing tests.**
- [ ] **Step 3: Refactor.**
- [ ] **Step 4: Tests pass.**
- [ ] **Step 5: Commit.**

```bash
git commit -m "refactor(mail): batch non-delta per-folder fan-out in catalog crawl"
```

---

### Task 27: `onedrive/cli/audit_sharing.py` per-item permissions

**Files:** `src/m365ctl/onedrive/cli/audit_sharing.py`, `tests/test_cli_audit_sharing.py`

Today: per-item `GET /drives/{drive}/items/{id}/permissions` in a loop. Convert to chunks of 20.

- [ ] Steps 1-4.

```bash
git commit -m "refactor(onedrive): batch per-item permissions GETs in audit_sharing"
```

---

### Task 28: `onedrive/inventory.py` non-delta per-item metadata

**Files:** `src/m365ctl/onedrive/inventory.py`, `tests/test_cli_inventory.py`

Identify non-delta per-item GETs and convert to batched. Skip delta-token paths.

- [ ] Steps 1-4.

```bash
git commit -m "refactor(onedrive): batch non-delta per-item metadata GETs in inventory"
```

---

## Phase 6 — Documentation & rollout

### Task 29: CHANGELOG entry and operator-facing docs

**Files:** `CHANGELOG.md`, `docs/safety.md` (or wherever audit-log behavior is documented).

- [ ] **Step 1: Add a CHANGELOG entry under `## [Unreleased]`:**

```markdown
## [Unreleased]

### Added
- `m365ctl` now uses Microsoft Graph `$batch` (≤20 sub-requests per HTTP call)
  to execute bulk `--from-plan` plans and read-side fan-out passes. Typical
  bulk plans (mail-move, mail-delete, mail-categorize, mail-read, mail-flag,
  onedrive-move, onedrive-rename) see ~5× speedup. No CLI flags changed.

### Changed
- Audit-log records: in bulk plan execution, all `start` records for a run
  now appear before any `end` records (previously they strictly interleaved
  per op). `op_id` linkage between `start` and `end` is preserved, so
  `m365ctl undo` and log-replay tools are unaffected. Crash safety is
  preserved: every `start` is durable before its corresponding flush.
```

- [ ] **Step 2: Add a brief operator-facing note in `docs/safety.md` (or appropriate doc) describing the audit-log behavior change.**

- [ ] **Step 3: Commit.**

```bash
git add CHANGELOG.md docs/
git commit -m "docs(batch): CHANGELOG entry + audit-log behavior note"
```

---

### Task 30: End-to-end sanity sweep

- [ ] **Step 1: Run the full test suite.**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 2: Run a smoke test against a real (test) tenant if one is available.**

This is optional and depends on tenant access. The recommended check:

```bash
m365ctl mail list --mailbox upn:test@... --folder /Inbox --limit 5
m365ctl mail move --plan-out /tmp/p.json --mailbox upn:test@... --folder /Inbox --to-folder /Archive --filter unread
m365ctl mail move --from-plan /tmp/p.json --confirm --mailbox upn:test@...
```

Watch the audit log to confirm `start`s precede `end`s as expected; confirm the move count matches plan count.

- [ ] **Step 3: Inspect generated audit-log entries** to confirm `op_id` linkage is intact between `start` / `end` records.

- [ ] **Step 4: If everything looks good, prepare a release commit.**

(No file changes — this is a verification task. Commit only if any nits surfaced.)

---

## Notes

- **Single-op CLI paths (`--message-id`, `--item-id`) are intentionally left untouched.** They go through `execute_<verb>(graph, ...)` which now wraps a one-call `EagerSession` internally; the HTTP behavior is identical to today.
- **`mail.send` / `mail.draft` / large-attachment uploads** are out of scope — they involve multi-step orchestration that doesn't fit a homogeneous batch.
- **`/delta` and `/search/query`** are not batched — see spec non-goals.
- **Parallel batch dispatch** (multiple `/$batch` POSTs in flight) is a follow-up; the current shape leaves the door open without changing call sites.

## Spec coverage check

Cross-reference against `docs/superpowers/specs/2026-05-01-graph-batch-support-design.md`:

- Goal / non-goals: addressed.
- Scope (3 families): Phase 3-4 covers families 1-2; Phase 5 covers family 3.
- Architecture / `BatchSession` + `BatchFuture`: Tasks 1-3.
- `EagerSession` adapter: Task 4.
- `GraphCaller` Protocol: Task 1.
- Bulk plan executor: Task 5.
- Verb refactor: Tasks 6-15 (mail) + 16-20 (OneDrive).
- Audit-log timing change: Tasks 6-20 inherit the change; documented in Task 29.
- Read-side fan-out: Tasks 21-28.
- Testing strategy: every task includes failing-first tests; `tests/test_batch.py` covers the primitive in Tasks 1-4.
- Risks: addressed in Tasks 1-3 (URL normalization, auth-strip, 204 handling, Retry-After at both levels, missing sub-response handling).
- File layout: matches spec.
- Rollout: Task 30.
