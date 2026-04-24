"""Shared helpers for shelling out to PnP.PowerShell scripts.

Consolidates three pieces of infrastructure used by multiple mutate
modules:

* :func:`invoke_pwsh` — runs ``pwsh -NoProfile -File <script> <args>`` and
  returns ``(returncode, stdout, stderr)``. Callers patch
  ``fazla_od.mutate._pwsh.subprocess.run`` in tests.
* :func:`lookup_site_url_from_drive_id` — resolves a Graph ``drive_id`` to
  the owning SharePoint/OneDrive site URL (by trimming the default-library
  suffix off ``webUrl``). Used by every PnP.PowerShell fallback path.
* :func:`normalize_recycle_dir_name` — converts a Graph-style parent_path
  (``/drives/<id>/root:/Folder``) to the site-relative tail PnP's
  ``Find-RecycleBinItem -DirName`` wildcard match expects.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fazla_od.graph import GraphClient, GraphError


# Path to the repo's PowerShell scripts directory, for module-level
# constants like _RESTORE_PS1 / _PURGE_PS1 / _LABEL_PS1 to use as a base.
PS_SCRIPTS_DIR = Path(__file__).resolve().parents[2].parent / "scripts" / "ps"


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
            f"noWebUrl: drive {drive_id!r} has no webUrl; "
            "cannot derive site URL"
        )
    low = web_url.lower()
    for sfx in _WEB_URL_LIB_SUFFIXES:
        if low.endswith(sfx.lower()):
            return web_url[: -len(sfx)].rstrip("/")
    raise GraphError(
        f"unknownLibrarySuffix: cannot derive site URL from webUrl "
        f"{web_url!r}: expected suffix /Shared%20Documents, "
        "/Shared Documents, or /Documents"
    )


def normalize_recycle_dir_name(graph_path: str) -> str:
    """Strip the Graph-path prefix (everything up to and including 'root:')
    from a recorded parent_path, leaving a site-relative tail suitable for
    PnP's recycle-bin ``DirName`` wildcard match.

    PnP's ``Find-RecycleBinItem`` compares its ``-DirName`` wildcard
    against the site-relative ``DirName`` field of each recycle-bin item
    (e.g. ``personal/user_fazla_com/Documents/_fazla_smoke2``). If we pass
    the full Graph path (``/drives/<id>/root:/_fazla_smoke2``) the ``-like
    "*$DirName"`` match never fires and PnP reports ``NoMatch``.

    Examples:
      '/drives/b!.../root:/_fazla_smoke2' -> '_fazla_smoke2'
      '/drive/root:/Folder/Sub'           -> 'Folder/Sub'
      '/Folder/Sub'                       -> 'Folder/Sub'  (already normalized)
      ''                                  -> ''
    """
    if not graph_path:
        return ""
    marker = "root:"
    idx = graph_path.find(marker)
    if idx >= 0:
        tail = graph_path[idx + len(marker):]
    else:
        tail = graph_path
    return tail.lstrip("/")
