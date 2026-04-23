"""Thin httpx-backed Microsoft Graph client.

Intentionally minimal in Plan 1 - just enough to call /me and /applications
for whoami. Plan 2 will either extend this or swap to msgraph-sdk; the
interface here (a single ``get`` returning parsed JSON) is chosen so either
path is straightforward.
"""
from __future__ import annotations

from typing import Callable

import httpx

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphError(RuntimeError):
    """Raised when Graph returns a non-2xx response."""


class GraphClient:
    def __init__(
        self,
        *,
        token_provider: Callable[[], str],
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._token_provider = token_provider
        self._client = httpx.Client(
            base_url=GRAPH_BASE,
            transport=transport,
            timeout=timeout,
        )

    def get(self, path: str, *, params: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._token_provider()}"}
        resp = self._client.get(path, headers=headers, params=params)
        if resp.status_code >= 400:
            body = resp.json() if resp.content else {}
            err = body.get("error", {})
            code = err.get("code", f"HTTP{resp.status_code}")
            msg = err.get("message", resp.text[:200])
            raise GraphError(f"{code}: {msg}")
        return resp.json()

    def close(self) -> None:
        self._client.close()
