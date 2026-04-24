from __future__ import annotations

import httpx
import pytest

from m365ctl.common.graph import GraphClient, GraphError


def test_get_attaches_bearer_token() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = GraphClient(token_provider=lambda: "abc123", transport=transport)

    result = client.get("/me")

    assert result == {"ok": True}
    assert captured["auth"] == "Bearer abc123"


def test_get_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"code": "InvalidAuthenticationToken", "message": "bad"}},
        )

    client = GraphClient(
        token_provider=lambda: "x", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(GraphError, match="InvalidAuthenticationToken"):
        client.get("/me")


def test_graph_get_bytes_returns_raw_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"payload-bytes")

    transport = httpx.MockTransport(handler)
    graph = GraphClient(
        token_provider=lambda: "tok", transport=transport, sleep=lambda _s: None
    )
    assert graph.get_bytes("/me/messages/m1/attachments/a1/$value") == b"payload-bytes"


def test_graph_patch_accepts_extra_headers():
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(dict(request.headers))
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    graph = GraphClient(token_provider=lambda: "tok", transport=transport, sleep=lambda _s: None)
    graph.patch("/me/messages/m1", json_body={"isRead": True}, headers={"If-Match": "etag-123"})
    assert captured[0]["if-match"] == "etag-123"


def test_graph_post_accepts_extra_headers():
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(dict(request.headers))
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    graph = GraphClient(token_provider=lambda: "tok", transport=transport, sleep=lambda _s: None)
    graph.post("/search/query", json={"requests": []}, headers={"X-Custom": "abc"})
    assert captured[0]["x-custom"] == "abc"
