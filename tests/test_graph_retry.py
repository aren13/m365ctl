from __future__ import annotations

from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from m365ctl.graph import GraphClient, GraphError, is_transient_graph_error


def _seq_handler(responses: list[httpx.Response]):
    it = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return next(it)

    return handler


def test_get_retries_on_429_and_honours_retry_after_seconds() -> None:
    sleeps: list[float] = []
    transport = httpx.MockTransport(
        _seq_handler(
            [
                httpx.Response(
                    429,
                    headers={"Retry-After": "2"},
                    json={"error": {"code": "TooManyRequests", "message": "slow down"}},
                ),
                httpx.Response(200, json={"ok": True}),
            ]
        )
    )
    client = GraphClient(
        token_provider=lambda: "t",
        transport=transport,
        sleep=sleeps.append,
        max_attempts=3,
    )
    result = client.get("/me")
    assert result == {"ok": True}
    assert sleeps == [2.0]


def test_get_retries_on_503_with_http_date_retry_after() -> None:
    when = datetime.now(timezone.utc) + timedelta(seconds=3)
    sleeps: list[float] = []
    transport = httpx.MockTransport(
        _seq_handler(
            [
                httpx.Response(
                    503,
                    headers={"Retry-After": format_datetime(when)},
                    json={"error": {"code": "serviceNotAvailable", "message": "x"}},
                ),
                httpx.Response(200, json={"ok": True}),
            ]
        )
    )
    client = GraphClient(
        token_provider=lambda: "t",
        transport=transport,
        sleep=sleeps.append,
        max_attempts=3,
    )
    client.get("/me")
    # Allow slack for clock drift; delay should be approx 3s, clamped >= 0.
    assert len(sleeps) == 1
    assert 0.0 <= sleeps[0] <= 4.0


def test_get_gives_up_after_max_attempts() -> None:
    transport = httpx.MockTransport(
        _seq_handler(
            [httpx.Response(429, headers={"Retry-After": "0"},
                            json={"error": {"code": "TooManyRequests", "message": "x"}})]
            * 5
        )
    )
    client = GraphClient(
        token_provider=lambda: "t",
        transport=transport,
        sleep=lambda _: None,
        max_attempts=3,
    )
    with pytest.raises(Exception) as exc_info:
        client.get("/me")
    # Either RetryExhausted or the underlying GraphError surfaces; both OK.
    assert "TooManyRequests" in str(exc_info.value) or "giving up" in str(exc_info.value)


def test_non_transient_error_not_retried() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            401, json={"error": {"code": "InvalidAuthenticationToken", "message": "bad"}}
        )

    client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
        max_attempts=5,
    )
    with pytest.raises(GraphError, match="InvalidAuthenticationToken"):
        client.get("/me")
    assert calls["n"] == 1


def test_retry_after_attribute_set_on_graph_error() -> None:
    transport = httpx.MockTransport(
        _seq_handler(
            [
                httpx.Response(
                    429,
                    headers={"Retry-After": "7"},
                    json={"error": {"code": "TooManyRequests", "message": "x"}},
                )
            ]
        )
    )
    # With max_attempts=1 the first failure is re-raised directly; check attr.
    client = GraphClient(
        token_provider=lambda: "t",
        transport=transport,
        sleep=lambda _: None,
        max_attempts=1,
    )
    with pytest.raises(GraphError) as exc_info:
        client.get("/me")
    assert exc_info.value.retry_after_seconds == 7.0
    assert is_transient_graph_error(exc_info.value)
