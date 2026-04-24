"""Shared helpers for shelling out to PnP.PowerShell scripts.

Consolidates two pieces of infrastructure used by multiple mutate modules:

* :func:`invoke_pwsh` — runs ``pwsh -NoProfile -File <script> <args>`` and
  returns ``(returncode, stdout, stderr)``. Callers patch
  ``fazla_od.mutate._pwsh.subprocess.run`` in tests.
* :func:`lookup_site_url_from_drive_id` — resolves a Graph ``drive_id`` to
  the owning SharePoint/OneDrive site URL (by trimming the default-library
  suffix off ``webUrl``). Used by every PnP.PowerShell fallback path.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fazla_od.graph import GraphClient, GraphError


def invoke_pwsh(script_path: Path | str, args: list[str]) -> tuple[int, str, str]:
    """Run ``pwsh -NoProfile -File <script> <args>`` and return its result.

    Returns ``(returncode, stdout, stderr)``.

    Raises :class:`FileNotFoundError` if ``pwsh`` is not on PATH; callers
    decide what that means (typically "PnP fallback unavailable — fall
    through to the legacy manual-instructions wrap").
    """
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(script_path), *args],
        capture_output=True, text=True, check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


# webUrl suffixes we strip to derive the site URL from a drive's webUrl.
# Order matters: longer/more-specific first so we don't accidentally eat
# 'Documents' from a library-named-literally-'Documents' while a
# '/Shared Documents' suffix was the real thing.
_WEB_URL_LIB_SUFFIXES = (
    "/Shared%20Documents",
    "/Shared Documents",
    "/Documents",
)


def lookup_site_url_from_drive_id(graph: GraphClient, drive_id: str) -> str:
    """Return the SharePoint/OneDrive site URL that owns ``drive_id``.

    Calls ``GET /drives/{drive_id}``, reads ``webUrl``, and trims the
    trailing default-library segment. The site URL is returned as-is
    (URL-encoded spaces preserved in the host/path prefix) — callers
    forward it straight to ``Connect-PnPOnline -Url ...``.

    Raises :class:`GraphError` with code ``noWebUrl`` if the drive has no
    ``webUrl``, or ``unknownLibrarySuffix`` if the ``webUrl`` doesn't end
    in a known default-library suffix (we refuse to guess — a wrong site
    URL combined with ``Find-RecycleBinItem``'s wildcard match is a
    data-recovery hazard).
    """
    body = graph.get(f"/drives/{drive_id}")
    web_url = (body.get("webUrl") or "").rstrip("/")
    if not web_url:
        raise GraphError(
            "noWebUrl",
            f"drive {drive_id!r} has no webUrl; cannot derive site URL",
            status_code=None,
        )
    low = web_url.lower()
    for sfx in _WEB_URL_LIB_SUFFIXES:
        if low.endswith(sfx.lower()):
            return web_url[: -len(sfx)].rstrip("/")
    raise GraphError(
        "unknownLibrarySuffix",
        f"cannot derive site URL from webUrl {web_url!r}: expected suffix "
        "/Shared%20Documents, /Shared Documents, or /Documents",
        status_code=None,
    )
