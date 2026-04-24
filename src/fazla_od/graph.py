"""Thin httpx-backed Microsoft Graph client.

Plan 3 changes:
- ``GraphError`` carries a ``retry_after_seconds`` attribute (``None`` when
  absent / unparseable).
- ``GraphClient`` accepts ``sleep`` and ``max_attempts`` and wraps each
  ``get`` / ``get_absolute`` call in ``fazla_od.retry.with_retry``, treating
  429/503 (and 500/502/504) as transient.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Iterator

import httpx

from fazla_od.retry import with_retry

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

    ``retry_after_seconds`` is populated from the ``Retry-After`` header when
    present; ``None`` otherwise.
    """

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def is_transient_graph_error(exc: Exception) -> bool:
    if not isinstance(exc, GraphError):
        return False
    head = str(exc).split(":", 1)[0].strip()
    return head in _TRANSIENT_CODES


def _retry_after_of(exc: Exception) -> float | None:
    if isinstance(exc, GraphError):
        return exc.retry_after_seconds
    return None


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    v = value.strip()
    # Integer seconds first.
    try:
        return max(0.0, float(v))
    except ValueError:
        pass
    # HTTP-date fallback (RFC 7231 §7.1.3).
    try:
        dt = parsedate_to_datetime(v)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = (dt - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, delta)


class GraphClient:
    def __init__(
        self,
        *,
        token_provider: Callable[[], str],
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
        sleep: Callable[[float], None] | None = None,
        max_attempts: int = 5,
    ) -> None:
        self._token_provider = token_provider
        self._client = httpx.Client(
            base_url=GRAPH_BASE,
            transport=transport,
            timeout=timeout,
        )
        # ``sleep`` defaults to ``time.sleep``; tests inject a capturing
        # ``list.append`` to assert on back-off delays without real sleeping.
        self._sleep = sleep if sleep is not None else time.sleep
        self._max_attempts = max_attempts

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token_provider()}"}

    def _retry(self, fn):
        return with_retry(
            fn,
            max_attempts=self._max_attempts,
            sleep=self._sleep,
            is_transient=is_transient_graph_error,
            retry_after_of=_retry_after_of,
        )

    def get(self, path: str, *, params: dict | None = None) -> dict:
        def _do() -> dict:
            resp = self._client.get(path, headers=self._auth_headers(), params=params)
            return self._parse(resp)

        return self._retry(_do)

    def get_absolute(self, url: str) -> dict:
        """GET an absolute URL (e.g. an @odata.nextLink)."""

        def _do() -> dict:
            resp = self._client.get(url, headers=self._auth_headers())
            return self._parse(resp)

        return self._retry(_do)

    def post(self, path: str, *, json: dict) -> dict:
        """POST with auto-retry; used by /search/query."""

        def _do() -> dict:
            resp = self._client.post(path, headers=self._auth_headers(), json=json)
            return self._parse(resp)

        return self._retry(_do)

    def get_paginated(
        self, path: str, *, params: dict | None = None
    ) -> Iterator[tuple[list[dict], str | None]]:
        """Yield (items, delta_link) for each page (auto-retrying per page)."""
        body = self.get(path, params=params)
        while True:
            items = body.get("value", [])
            next_link = body.get("@odata.nextLink")
            delta_link = body.get("@odata.deltaLink")
            yield items, delta_link
            if not next_link:
                return
            body = self.get_absolute(next_link)

    def patch(self, path: str, *, json_body: dict) -> dict:
        """PATCH with JSON body; returns parsed dict; wrapped with _retry."""

        def _do() -> dict:
            resp = self._client.patch(path, headers=self._auth_headers(), json=json_body)
            return self._parse(resp)

        return self._retry(_do)

    def post_raw(self, path: str, *, json_body: dict | None = None) -> httpx.Response:
        """POST returning the raw httpx.Response (e.g. to inspect Location on 202).

        Raises ``GraphError`` on 4xx/5xx so retry/classification still applies.
        Wrapped with _retry.
        """

        def _do() -> httpx.Response:
            resp = self._client.post(path, headers=self._auth_headers(), json=json_body)
            self._maybe_raise(resp)
            return resp

        return self._retry(_do)

    def delete(self, path: str) -> None:
        """DELETE; returns None on 204; parses body on non-204; raises on 4xx/5xx."""

        def _do() -> None:
            resp = self._client.delete(path, headers=self._auth_headers())
            if resp.status_code == 204:
                return None
            # Some endpoints (e.g. permanentDelete) return 200 + body.
            self._parse(resp)
            return None

        return self._retry(_do)

    def _maybe_raise(self, resp: httpx.Response) -> None:
        """Raise ``GraphError`` if *resp* is 4xx/5xx; return None otherwise."""
        if resp.status_code >= 400:
            try:
                body = resp.json() if resp.content else {}
            except ValueError:
                body = {}
            err = body.get("error", {}) if isinstance(body, dict) else {}
            code = err.get("code", f"HTTP{resp.status_code}")
            msg = err.get("message", resp.text[:200])
            raise GraphError(
                f"{code}: {msg}",
                retry_after_seconds=_parse_retry_after(resp.headers.get("Retry-After")),
            )

    def _parse(self, resp: httpx.Response) -> dict:
        if resp.status_code >= 400:
            try:
                body = resp.json() if resp.content else {}
            except ValueError:
                body = {}
            err = body.get("error", {}) if isinstance(body, dict) else {}
            code = err.get("code", f"HTTP{resp.status_code}")
            msg = err.get("message", resp.text[:200])
            raise GraphError(
                f"{code}: {msg}",
                retry_after_seconds=_parse_retry_after(resp.headers.get("Retry-After")),
            )
        if not resp.content:
            return {}
        return resp.json()

    def close(self) -> None:
        self._client.close()
