from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from fazla_od.download.fetcher import FetchResult, fetch_item


def _transport_redirect_then_200(body: bytes, redirect_url: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.microsoft.com":
            return httpx.Response(302, headers={"Location": redirect_url})
        # CDN — any other host — returns file bytes. Test that no Authorization.
        assert "authorization" not in {k.lower() for k in request.headers.keys()}
        return httpx.Response(200, content=body,
                              headers={"Content-Length": str(len(body))})

    return httpx.MockTransport(handler)


def test_fetch_writes_file(tmp_path: Path) -> None:
    body = b"hello" * 2000
    transport = _transport_redirect_then_200(body, "https://cdn.example/blob/abc")
    dest = tmp_path / "nested" / "a.bin"
    result = fetch_item(
        drive_id="d", item_id="i", dest=dest,
        token_provider=lambda: "t", transport=transport, overwrite=False,
    )
    assert isinstance(result, FetchResult)
    assert result.bytes_written == len(body)
    assert result.skipped is False
    assert dest.read_bytes() == body


def test_fetch_skips_existing_by_default(tmp_path: Path) -> None:
    dest = tmp_path / "a.bin"
    dest.write_bytes(b"old")

    # If we actually hit the network, the test will fail (handler asserts).
    def handler(_req):
        raise AssertionError("should not have been called")

    result = fetch_item(
        drive_id="d", item_id="i", dest=dest,
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        overwrite=False,
    )
    assert result.skipped is True
    assert dest.read_bytes() == b"old"


def test_fetch_overwrites_when_requested(tmp_path: Path) -> None:
    dest = tmp_path / "a.bin"
    dest.write_bytes(b"old")
    transport = _transport_redirect_then_200(b"NEW", "https://cdn.example/blob/abc")
    result = fetch_item(
        drive_id="d", item_id="i", dest=dest,
        token_provider=lambda: "t", transport=transport, overwrite=True,
    )
    assert result.skipped is False
    assert dest.read_bytes() == b"NEW"


def test_fetch_raises_on_non_redirect_non_200(tmp_path: Path) -> None:
    def handler(req):
        return httpx.Response(
            404, json={"error": {"code": "itemNotFound", "message": "gone"}}
        )

    with pytest.raises(Exception, match="itemNotFound|HTTP404"):
        fetch_item(
            drive_id="d", item_id="i", dest=tmp_path / "x",
            token_provider=lambda: "t",
            transport=httpx.MockTransport(handler), overwrite=True,
        )
