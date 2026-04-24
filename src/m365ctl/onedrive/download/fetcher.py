"""Streaming file download for OneDrive items.

Graph's `/drives/{d}/items/{i}/content` replies with 302 to a pre-signed CDN
URL. We follow the redirect manually and fetch without Authorization (the
CDN rejects bearer auth). The response body is streamed to disk in 1 MiB
chunks so multi-GB files don't blow up memory.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from m365ctl.common.graph import GraphError

_CHUNK = 1024 * 1024  # 1 MiB


@dataclass(frozen=True)
class FetchResult:
    drive_id: str
    item_id: str
    dest: Path
    bytes_written: int
    skipped: bool


def fetch_item(
    *,
    drive_id: str,
    item_id: str,
    dest: Path,
    token_provider: Callable[[], str],
    transport: httpx.BaseTransport | None = None,
    overwrite: bool = False,
    timeout: float = 300.0,
) -> FetchResult:
    if dest.exists() and not overwrite:
        return FetchResult(
            drive_id=drive_id, item_id=item_id, dest=dest,
            bytes_written=0, skipped=True,
        )

    dest.parent.mkdir(parents=True, exist_ok=True)

    content_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/content"

    # Step 1: hit Graph without following redirects so we can capture Location.
    with httpx.Client(transport=transport, timeout=timeout, follow_redirects=False) as c:
        resp = c.get(content_url, headers={"Authorization": f"Bearer {token_provider()}"})
        if resp.status_code in (301, 302, 303, 307, 308):
            target = resp.headers.get("Location")
            if not target:
                raise GraphError(f"HTTP{resp.status_code}: redirect without Location")
        elif resp.status_code == 200:
            # Rare: some drives stream content directly. Write it out.
            return _write_stream(dest, resp, drive_id, item_id)
        else:
            try:
                body = resp.json() if resp.content else {}
            except ValueError:
                body = {}
            err = body.get("error", {}) if isinstance(body, dict) else {}
            code = err.get("code", f"HTTP{resp.status_code}")
            msg = err.get("message", resp.text[:200])
            raise GraphError(f"{code}: {msg}")

    # Step 2: fetch the signed CDN URL without auth.
    with httpx.Client(transport=transport, timeout=timeout, follow_redirects=True) as c:
        with c.stream("GET", target) as r:
            if r.status_code != 200:
                raise GraphError(f"HTTP{r.status_code}: CDN fetch failed")
            total = 0
            with dest.open("wb") as f:
                for chunk in r.iter_bytes(_CHUNK):
                    f.write(chunk)
                    total += len(chunk)
    return FetchResult(
        drive_id=drive_id, item_id=item_id, dest=dest,
        bytes_written=total, skipped=False,
    )


def _write_stream(dest: Path, resp: httpx.Response, drive_id: str, item_id: str) -> FetchResult:
    total = 0
    with dest.open("wb") as f:
        for chunk in resp.iter_bytes(_CHUNK):
            f.write(chunk)
            total += len(chunk)
    return FetchResult(
        drive_id=drive_id, item_id=item_id, dest=dest,
        bytes_written=total, skipped=False,
    )
