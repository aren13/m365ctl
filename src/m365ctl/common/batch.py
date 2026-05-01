"""Microsoft Graph $batch support for m365ctl.

Two key types (full functionality lands in subsequent tasks):

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
