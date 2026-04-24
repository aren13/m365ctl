"""OneDrive DELETE (recycle) + RESTORE (from recycle).

Spec §7 rule 6: no hard deletes here. The Graph ``DELETE
/drives/{d}/items/{i}`` endpoint on OneDrive is a SOFT delete — the item
goes to the recycle bin. Hard-delete lives in ``mutate/clean.py``
(``od-clean recycle-bin``).

**Restore caveat (discovered during Plan 4 live smoke test):** Microsoft
Graph v1.0's ``POST /drives/{d}/items/{i}/restore`` is documented as
**OneDrive Personal only**. OneDrive-for-Business recycle-bin items have
no public Graph v1.0 restore endpoint — the supported paths are the
SharePoint REST API (``/Web/RecycleBin('<id>')/Restore()``) or
PnP.PowerShell (``Restore-PnPRecycleBinItem``).

``execute_restore`` attempts the Graph call first (still the right path
for OneDrive-Personal). On ``notSupported`` / ``BadRequest`` /
``invalidRequest`` it falls back to
``scripts/ps/recycle-restore.ps1`` (see Plan 5 Task 2) which uses
PnP.PowerShell. If ``Config`` is not supplied (e.g. tests or legacy
callers) or ``pwsh`` is not on PATH, the function falls through to the
legacy "manual instructions" error wrap so operators still know what to
do by hand.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fazla_od.audit import AuditLogger, log_mutation_end, log_mutation_start
from fazla_od.config import Config
from fazla_od.graph import GraphClient, GraphError
from fazla_od.planfile import Operation

_RESTORE_PS1 = (
    Path(__file__).resolve().parents[2].parent
    / "scripts" / "ps" / "recycle-restore.ps1"
)


@dataclass(frozen=True)
class DeleteResult:
    op_id: str
    status: str
    error: str | None = None
    after: dict[str, Any] | None = None


def execute_recycle_delete(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> DeleteResult:
    log_mutation_start(
        logger, op_id=op.op_id, cmd="od-delete",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id,
        before=before,
    )
    try:
        graph.delete(f"/drives/{op.drive_id}/items/{op.item_id}")
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return DeleteResult(op_id=op.op_id, status="error", error=str(e))
    after = {"parent_path": "(recycle bin)", "name": before.get("name", ""),
             "recycled_from": before.get("parent_path", "")}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return DeleteResult(op_id=op.op_id, status="ok", after=after)


_ODFB_RESTORE_MANUAL = (
    "Graph v1.0 /restore is OneDrive-Personal-only; "
    "OneDrive-for-Business items must be restored via SharePoint web UI "
    "(Recycle bin → Restore) or PnP.PowerShell "
    "(Restore-PnPRecycleBinItem). The original parent path is recorded in "
    "the audit log's 'before.parent_path' field for this op_id."
)

# Graph error codes that signal "this is the OneDrive-for-Business no-public-
# restore-endpoint case"; trigger the PnP.PowerShell fallback.
_ODFB_RESTORE_TOKENS = ("notSupported", "BadRequest", "invalidRequest")

# webUrl suffixes we strip to derive the site URL from a drive's webUrl. Order
# matters: longer/more-specific first so we don't accidentally eat 'Documents'
# from a library-named-literally-'Documents' while a '/Shared Documents' suffix
# was the real thing.
_WEB_URL_LIB_SUFFIXES = (
    "/Shared%20Documents",
    "/Shared Documents",
    "/Documents",
)


def _lookup_site_url(graph: GraphClient, drive_id: str) -> str:
    """Return the SharePoint/OneDrive site URL that owns ``drive_id``.

    Calls ``GET /drives/{drive_id}``, reads ``webUrl``, and trims the
    trailing default-library segment. The site URL is returned as-is
    (URL-encoded spaces preserved in the host/path prefix) — callers
    forward it straight to ``Connect-PnPOnline -Url ...``.
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
    # No known library suffix; fall back to the parent-of-path heuristic:
    # drop the last path segment, on the assumption webUrl ends at the
    # library root. Better than guessing.
    return unquote(web_url).rsplit("/", 1)[0].rstrip("/") or web_url


def _restore_via_pnp(
    op: Operation, before: dict[str, Any], cfg: Config, graph: GraphClient,
) -> tuple[dict[str, Any] | None, str | None]:
    """Shell out to scripts/ps/recycle-restore.ps1.

    Returns ``(after, None)`` on success, ``(None, error_str)`` on
    PS-script failure. Raises ``FileNotFoundError`` if pwsh is not on
    PATH — callers treat that as "fallback unavailable" and revert to
    the manual-instructions message.
    """
    site_url = _lookup_site_url(graph, op.drive_id)
    leaf = before.get("name", "")
    dir_name = before.get("parent_path", "")
    proc = subprocess.run(
        [
            "pwsh", "-NoProfile", "-File", str(_RESTORE_PS1),
            "-Tenant", cfg.tenant_id,
            "-ClientId", cfg.client_id,
            "-SiteUrl", site_url,
            "-LeafName", leaf,
            "-DirName", dir_name,
            "-PfxPath", str(cfg.cert_path),
        ],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return None, err or f"pwsh exited with code {proc.returncode}"
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as e:
        return None, f"could not parse PnP restore JSON: {e}: {proc.stdout!r}"
    after = {
        "parent_path": payload.get("restored_parent_path", dir_name),
        "name": payload.get("restored_name", leaf),
        "recycle_bin_item_id": payload.get("recycle_bin_item_id"),
    }
    return after, None


def execute_restore(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
    cfg: Config | None = None,
) -> DeleteResult:
    log_mutation_start(
        logger, op_id=op.op_id, cmd="od-undo(restore)",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id,
        before=before,
    )
    try:
        body = {"parentReference": {"id": op.args["parent_item_id"]}} \
            if "parent_item_id" in op.args else None
        resp = graph.post_raw(
            f"/drives/{op.drive_id}/items/{op.item_id}/restore",
            json_body=body,
        )
        data = resp.json() if resp.content else {}
    except GraphError as e:
        err = str(e)
        if any(t in err for t in _ODFB_RESTORE_TOKENS) and cfg is not None:
            try:
                after, pnp_err = _restore_via_pnp(op, before, cfg, graph)
            except FileNotFoundError:
                # pwsh not installed — fall back to legacy manual message.
                err = f"{err} | {_ODFB_RESTORE_MANUAL}"
                log_mutation_end(logger, op_id=op.op_id, after=None,
                                 result="error", error=err)
                return DeleteResult(op_id=op.op_id, status="error", error=err)
            if after is not None:
                log_mutation_end(logger, op_id=op.op_id, after=after,
                                 result="ok")
                return DeleteResult(op_id=op.op_id, status="ok", after=after)
            # PS call ran but failed — propagate its stderr as-is.
            log_mutation_end(logger, op_id=op.op_id, after=None,
                             result="error", error=pnp_err)
            return DeleteResult(op_id=op.op_id, status="error", error=pnp_err)
        if any(t in err for t in _ODFB_RESTORE_TOKENS):
            # ODfB token but no Config — can't run PS. Legacy wrap.
            err = f"{err} | {_ODFB_RESTORE_MANUAL}"
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=err)
        return DeleteResult(op_id=op.op_id, status="error", error=err)
    after = {
        "parent_path": (data.get("parentReference") or {}).get("path", ""),
        "name": data.get("name", before.get("name", "")),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return DeleteResult(op_id=op.op_id, status="ok", after=after)
