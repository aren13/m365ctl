from __future__ import annotations

import httpx
import pytest

from m365ctl.graph import GraphClient, GraphError


def test_patch_sends_json_body_with_bearer() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = request.content.decode()
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"id": "i1", "name": "new.txt"})

    client = GraphClient(
        token_provider=lambda: "tkn",
        transport=httpx.MockTransport(handler),
    )
    body = client.patch("/drives/d1/items/i1", json_body={"name": "new.txt"})

    assert seen["method"] == "PATCH"
    assert '"name":"new.txt"' in seen["body"].replace(" ", "")
    assert seen["auth"] == "Bearer tkn"
    assert body["id"] == "i1"


def test_post_raw_returns_response_with_location_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            202,
            headers={"Location": "https://graph/copy-monitor/abc"},
            json={},
        )

    client = GraphClient(
        token_provider=lambda: "tkn",
        transport=httpx.MockTransport(handler),
    )
    resp = client.post_raw(
        "/drives/d1/items/i1/copy",
        json_body={"parentReference": {"id": "p"}},
    )
    assert resp.status_code == 202
    assert resp.headers["location"] == "https://graph/copy-monitor/abc"


def test_delete_returns_none_on_204() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    client = GraphClient(
        token_provider=lambda: "tkn",
        transport=httpx.MockTransport(handler),
    )
    assert client.delete("/drives/d1/items/i1") is None


def test_patch_raises_graph_error_on_4xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"error": {"code": "accessDenied", "message": "nope"}}
        )

    client = GraphClient(
        token_provider=lambda: "tkn",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(GraphError, match="accessDenied"):
        client.patch("/drives/d1/items/i1", json_body={"name": "x"})


def test_patch_retries_on_429_then_succeeds() -> None:
    """Confirms the mutation verbs are wrapped in with_retry."""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                json={"error": {"code": "TooManyRequests", "message": "slow"}},
            )
        return httpx.Response(200, json={"id": "i1"})

    client = GraphClient(
        token_provider=lambda: "tkn",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,  # zero delay in tests
    )
    result = client.patch("/drives/d1/items/i1", json_body={"name": "y"})
    assert result == {"id": "i1"}
    assert attempts["n"] == 3


def test_patch_gives_up_after_max_attempts() -> None:
    from m365ctl.retry import RetryExhausted

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"error": {"code": "serviceNotAvailable", "message": "down"}},
        )

    client = GraphClient(
        token_provider=lambda: "tkn",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
        max_attempts=3,  # NOT max_retry_attempts — existing kwarg name
    )
    with pytest.raises(RetryExhausted):
        client.patch("/drives/d1/items/i1", json_body={"name": "y"})
