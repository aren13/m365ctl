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
