"""Tests for m365ctl.common.batch."""
from __future__ import annotations

import json

import httpx
import pytest

from m365ctl.common.batch import (
    BatchFuture,
    BatchSession,
    BatchUnflushedError,
    _Resolved,
)
from m365ctl.common.graph import GraphClient, GraphError


def test_batch_future_unflushed_raises():
    f = BatchFuture(req_id="1")
    with pytest.raises(BatchUnflushedError):
        f.result()
    with pytest.raises(BatchUnflushedError):
        f.status()
    with pytest.raises(BatchUnflushedError):
        f.headers()
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
        # Zero requests have hit the transport yet.
        assert len(captured) == 0
    # After `with` exit: exactly one /$batch POST and futures resolved.
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
        # Authorization header MUST NOT appear in the sub-request; If-Match must.
        b.delete("/me/messages/m1", headers={"Authorization": "Bearer LEAK", "If-Match": "etag-1"})

    assert captured[0]["requests"][0]["url"] == "me/messages/m1"
    sub_headers = captured[0]["requests"][0].get("headers", {})
    sub_keys_lower = {k.lower() for k in sub_headers}
    assert "authorization" not in sub_keys_lower
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


def test_batch_session_get_absolute_strips_graph_host():
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        captured.append(payload)
        return httpx.Response(200, json={
            "responses": [{"id": "1", "status": 200, "headers": {}, "body": {}}],
        })

    graph = _stub_graph(handler)
    with graph.batch() as b:
        # @odata.nextLink-style absolute URLs must be normalized to bare paths.
        b.get_absolute("https://graph.microsoft.com/v1.0/me/messages?$skip=10")
    assert captured[0]["requests"][0]["url"] == "me/messages?$skip=10"


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
