"""Tests for m365ctl.common.batch."""
from __future__ import annotations

import pytest

from m365ctl.common.batch import (
    BatchFuture,
    BatchUnflushedError,
    _Resolved,
)
from m365ctl.common.graph import GraphError


def test_batch_future_unflushed_raises():
    f = BatchFuture(req_id="1")
    with pytest.raises(BatchUnflushedError):
        f.result()
    assert f.done() is False


def test_batch_future_resolves_with_body():
    f = BatchFuture(req_id="1")
    f._resolve(status=200, headers={}, body={"id": "m1"})
    assert f.done() is True
    assert f.result() == {"id": "m1"}
    assert f.status() == 200


def test_batch_future_resolves_with_error():
    f = BatchFuture(req_id="1")
    err = GraphError("ItemNotFound: gone")
    f._resolve_error(err)
    assert f.done() is True
    with pytest.raises(GraphError, match="ItemNotFound"):
        f.result()


def test_resolved_eager_returns_value():
    r = _Resolved(value={"ok": True})
    assert r.result() == {"ok": True}
    assert r.done() is True


def test_resolved_eager_raises_error():
    err = GraphError("BadRequest")
    r = _Resolved(error=err)
    with pytest.raises(GraphError):
        r.result()
