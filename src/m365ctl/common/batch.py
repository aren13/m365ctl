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
from typing import TYPE_CHECKING, Protocol

from m365ctl.common.graph import GraphError

if TYPE_CHECKING:
    from m365ctl.common.graph import GraphClient


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


@dataclass(frozen=True)
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


_BATCH_MAX = 20


def _normalize_path(path: str) -> str:
    """Strip a leading slash. Graph $batch sub-requests want bare paths."""
    return path[1:] if path.startswith("/") else path


def _strip_auth(headers: dict[str, str] | None) -> dict[str, str]:
    """Remove any Authorization variant; the outer /$batch POST owns auth."""
    if not headers:
        return {}
    return {k: v for k, v in headers.items() if k.lower() != "authorization"}


def _build_subrequest(req_id: str, method: str, path: str,
                      body: dict | None = None,
                      headers: dict[str, str] | None = None) -> dict:
    sub: dict = {"id": req_id, "method": method, "url": _normalize_path(path)}
    clean_headers = _strip_auth(headers)
    if body is not None:
        sub["body"] = body
        clean_headers.setdefault("Content-Type", "application/json")
    if clean_headers:
        sub["headers"] = clean_headers
    return sub


def _is_transient_status(status: int, code: str) -> bool:
    """Mirror m365ctl.common.graph._TRANSIENT_CODES for sub-response retry."""
    if status in (429, 500, 502, 503, 504):
        return True
    # Lazy: import here to avoid module-level coupling with graph.py.
    from m365ctl.common.graph import _TRANSIENT_CODES
    return code in _TRANSIENT_CODES


class BatchSession:
    """Buffers Graph calls into /$batch envelopes (<=20 sub-requests each).

    Use as a context manager:

        with graph.batch() as b:
            f = b.get("/me/messages/m1")
            ...
        # `with` exit flushed; f.result() now safe.

    Auto-flush fires when the 20th call is buffered. ``.result()`` on a
    future before its session has flushed raises ``BatchUnflushedError``.

    Note on ``get_absolute``: ``@odata.nextLink`` URLs include the Graph
    host prefix (``https://graph.microsoft.com/v1.0``). The session strips
    that prefix so sub-request URLs remain bare paths.
    """

    def __init__(self, graph: "GraphClient") -> None:
        self._graph = graph
        self._pending: list[tuple[BatchFuture, dict]] = []
        self._next_id = 0
        self._closed = False

    def __enter__(self) -> "BatchSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Always flush remaining buffered calls, even on exception, so
        # futures don't dangle in an unflushed state forever.
        try:
            if self._pending:
                self._flush_now()
        finally:
            self._closed = True

    def _new_id(self) -> str:
        self._next_id += 1
        return str(self._next_id)

    def _enqueue(self, method: str, path: str, *, body: dict | None,
                 headers: dict | None) -> BatchFuture:
        if self._closed:
            raise RuntimeError("BatchSession is closed")
        req_id = self._new_id()
        sub = _build_subrequest(req_id, method, path, body=body, headers=headers)
        f = BatchFuture(req_id=req_id)
        self._pending.append((f, sub))
        if len(self._pending) >= _BATCH_MAX:
            self._flush_now()
        return f

    def get(self, path: str, *, headers: dict | None = None) -> BatchFuture:
        return self._enqueue("GET", path, body=None, headers=headers)

    def get_absolute(self, url: str, *, headers: dict | None = None) -> BatchFuture:
        path = url
        for prefix in ("https://graph.microsoft.com/v1.0", "https://graph.microsoft.com/beta"):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break
        return self._enqueue("GET", path, body=None, headers=headers)

    def post(self, path: str, *, json: dict, headers: dict | None = None) -> BatchFuture:
        return self._enqueue("POST", path, body=json, headers=headers)

    def patch(self, path: str, *, json_body: dict, headers: dict | None = None) -> BatchFuture:
        return self._enqueue("PATCH", path, body=json_body, headers=headers)

    def delete(self, path: str, *, headers: dict | None = None) -> BatchFuture:
        return self._enqueue("DELETE", path, body=None, headers=headers)

    def flush(self) -> None:
        """Explicit flush; usually the `with` exit handles this."""
        if self._pending:
            self._flush_now()

    # Internal — actual /$batch dispatch.
    def _flush_now(self) -> None:
        batch = self._pending
        self._pending = []
        self._dispatch(batch)

    def _dispatch(self, batch: list[tuple["BatchFuture", dict]]) -> None:
        attempts_remaining = self._graph._max_attempts
        pending = list(batch)
        last_err_by_id: dict[str, GraphError] = {}

        while pending:
            envelope = {"requests": [sub for _f, sub in pending]}
            body = self._graph.post("/$batch", json=envelope)
            still_pending: list[tuple[BatchFuture, dict]] = []
            max_retry_after = 0.0
            seen_ids: set[str] = set()

            for resp in body.get("responses", []):
                req_id = str(resp.get("id"))
                seen_ids.add(req_id)
                # Find the (future, sub) pair matching this id.
                pair = next(((f, sub) for f, sub in pending if f._req_id == req_id), None)
                if pair is None:
                    continue
                f, sub = pair
                status = int(resp.get("status", 0))
                headers = {k: str(v) for k, v in (resp.get("headers") or {}).items()}
                sub_body = resp.get("body")
                if status < 400:
                    f._resolve(
                        status=status,
                        headers=headers,
                        body=sub_body if isinstance(sub_body, dict) else None,
                    )
                    continue
                err = self._graph_error_from_subresponse(status, sub_body, headers)
                last_err_by_id[req_id] = err
                code = str(err).split(":", 1)[0].strip()
                if attempts_remaining > 1 and _is_transient_status(status, code):
                    still_pending.append((f, sub))
                    if err.retry_after_seconds is not None:
                        max_retry_after = max(max_retry_after, err.retry_after_seconds)
                else:
                    f._resolve_error(err)

            # Sub-requests with no matching response → synthetic error.
            for f, _sub in pending:
                if f._req_id not in seen_ids and not f.done():
                    f._resolve_error(GraphError(
                        f"HTTP0: missing sub-response for id={f._req_id}",
                    ))

            if not still_pending:
                return
            attempts_remaining -= 1
            if attempts_remaining <= 0:
                for f, _sub in still_pending:
                    if not f.done():
                        f._resolve_error(
                            last_err_by_id.get(f._req_id,
                                               GraphError("HTTP429: retry exhausted"))
                        )
                return
            delay = max_retry_after if max_retry_after > 0 else 1.0
            self._graph._sleep(delay)
            pending = still_pending

    def _graph_error_from_subresponse(
        self, status: int, body: object, headers: dict[str, str],
    ) -> GraphError:
        from m365ctl.common.graph import _parse_retry_after
        err = (body.get("error") if isinstance(body, dict) else None) or {}
        code = err.get("code", f"HTTP{status}")
        msg = err.get("message", "")
        return GraphError(
            f"{code}: {msg}",
            retry_after_seconds=_parse_retry_after(headers.get("Retry-After")),
        )
