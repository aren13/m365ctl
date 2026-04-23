from __future__ import annotations

import httpx

from fazla_od.graph import GraphClient, GraphError, is_transient_graph_error


def _queue_handler(responses: list[httpx.Response]):
    it = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return next(it)

    return handler


def test_get_paginated_follows_next_links_and_returns_delta() -> None:
    p1 = httpx.Response(
        200,
        json={
            "value": [{"id": "a"}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/more?token=abc",
        },
    )
    p2 = httpx.Response(
        200,
        json={
            "value": [{"id": "b"}, {"id": "c"}],
            "@odata.deltaLink": "https://graph.microsoft.com/v1.0/done?token=xyz",
        },
    )
    client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(_queue_handler([p1, p2])),
    )

    pages = list(client.get_paginated("/me/drive/root/delta"))

    # Each yielded page is (items, delta_link_or_None)
    assert pages[0][0] == [{"id": "a"}]
    assert pages[0][1] is None
    assert pages[1][0] == [{"id": "b"}, {"id": "c"}]
    assert pages[1][1] == "https://graph.microsoft.com/v1.0/done?token=xyz"


def test_get_paginated_single_page_returns_delta() -> None:
    resp = httpx.Response(
        200,
        json={
            "value": [{"id": "only"}],
            "@odata.deltaLink": "https://graph.microsoft.com/v1.0/done?token=1",
        },
    )
    client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(_queue_handler([resp])),
    )
    pages = list(client.get_paginated("/me/drive/root/delta"))
    assert len(pages) == 1
    assert pages[0][0] == [{"id": "only"}]
    assert pages[0][1] == "https://graph.microsoft.com/v1.0/done?token=1"


def test_is_transient_graph_error_covers_429_503() -> None:
    for code in ("TooManyRequests", "serviceNotAvailable", "HTTP429", "HTTP503"):
        assert is_transient_graph_error(GraphError(f"{code}: test"))
    assert not is_transient_graph_error(GraphError("InvalidAuthenticationToken: bad"))
    assert not is_transient_graph_error(ValueError("unrelated"))
