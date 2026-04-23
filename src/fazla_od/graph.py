"""Thin httpx-backed Microsoft Graph client.

Plan 1 covered single-call GETs; Plan 2 adds:
- ``get_paginated``: yields (items, delta_link) tuples, following ``@odata.nextLink``.
- ``is_transient_graph_error``: boolean predicate for the retry helper.
"""
from __future__ import annotations

from typing import Callable, Iterator

import httpx

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_TRANSIENT_CODES = {
    "TooManyRequests",
    "serviceNotAvailable",
    "HTTP429",
    "HTTP500",
    "HTTP502",
    "HTTP503",
    "HTTP504",
}


class GraphError(RuntimeError):
    """Raised when Graph returns a non-2xx response.

    The first colon-separated token of ``str(err)`` is the Graph error code
    (or ``HTTP<status>`` fallback); use ``is_transient_graph_error`` to
    classify.
    """


def is_transient_graph_error(exc: Exception) -> bool:
    if not isinstance(exc, GraphError):
        return False
    head = str(exc).split(":", 1)[0].strip()
    return head in _TRANSIENT_CODES


class GraphClient:
    def __init__(
        self,
        *,
        token_provider: Callable[[], str],
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._token_provider = token_provider
        self._client = httpx.Client(
            base_url=GRAPH_BASE,
            transport=transport,
            timeout=timeout,
        )

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token_provider()}"}

    def get(self, path: str, *, params: dict | None = None) -> dict:
        resp = self._client.get(path, headers=self._auth_headers(), params=params)
        return self._parse(resp)

    def get_absolute(self, url: str) -> dict:
        """GET an absolute URL (e.g. an @odata.nextLink)."""
        resp = self._client.get(url, headers=self._auth_headers())
        return self._parse(resp)

    def get_paginated(
        self, path: str, *, params: dict | None = None
    ) -> Iterator[tuple[list[dict], str | None]]:
        """Yield (items, delta_link) for each page.

        ``delta_link`` is ``None`` on all pages except the last of a delta
        feed, where it is the ``@odata.deltaLink`` URL to pass back next
        time. Non-delta endpoints never see a delta_link.
        """
        body = self.get(path, params=params)
        while True:
            items = body.get("value", [])
            next_link = body.get("@odata.nextLink")
            delta_link = body.get("@odata.deltaLink")
            yield items, delta_link
            if not next_link:
                return
            body = self.get_absolute(next_link)

    def _parse(self, resp: httpx.Response) -> dict:
        if resp.status_code >= 400:
            body = resp.json() if resp.content else {}
            err = body.get("error", {})
            code = err.get("code", f"HTTP{resp.status_code}")
            msg = err.get("message", resp.text[:200])
            raise GraphError(f"{code}: {msg}")
        return resp.json()

    def close(self) -> None:
        self._client.close()
