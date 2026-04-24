"""Specialised cleanup ops: recycle-bin purge, old-versions, stale-shares.

**Recycle-bin purge caveat (discovered during Plan 4 live smoke test):**
Microsoft Graph v1.0's ``POST /drives/{d}/items/{i}/permanentDelete`` is
designed for *live* items (bypass-recycle-bin hard delete). Items
already in the recycle bin return HTTP 404 at that endpoint — there is
no public Graph v1.0 API to empty the OneDrive-for-Business recycle bin
by item id. Supported paths: SharePoint REST
(``/Web/RecycleBin('<rb_id>')/DeleteObject()``) or PnP.PowerShell
(``Clear-PnPRecycleBinItem``).

``purge_recycle_bin_item`` calls the Graph endpoint first (it works for
live items and for some tenants). On ``itemNotFound`` / ``HTTP404`` /
``accessDenied`` it falls back to ``scripts/ps/recycle-purge.ps1`` (see
Plan 5 Task 3) which uses PnP.PowerShell. If ``Config`` is not supplied
(e.g. tests or legacy callers) or ``pwsh`` is not on PATH, the function
falls through to the legacy "manual instructions" error wrap so operators
still know what to do by hand.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.config import Config
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.onedrive.mutate._pwsh import (
    PS_SCRIPTS_DIR,
    invoke_pwsh,
    lookup_site_url_from_drive_id,
    normalize_recycle_dir_name,
)
from m365ctl.common.planfile import Operation


_PURGE_PS1 = PS_SCRIPTS_DIR / "recycle-purge.ps1"


@dataclass(frozen=True)
class CleanResult:
    op_id: str
    status: str
    error: str | None = None
    after: dict[str, Any] | None = None


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


_ODFB_PURGE_MANUAL = (
    "Graph v1.0 /permanentDelete targets live items only; "
    "OneDrive-for-Business recycle-bin items must be purged via "
    "SharePoint web UI (Recycle bin → Delete), the SharePoint REST API "
    "(/Web/RecycleBin('<rb_id>')/DeleteObject()), or PnP.PowerShell "
    "(Clear-PnPRecycleBinItem)."
)

# Graph error codes that signal "the item is in the recycle bin, not live".
# (accessDenied shows up in some tenants; itemNotFound/HTTP404 is the common
# case observed during the Plan 4 live smoke.)
_ODFB_PURGE_TOKENS = ("itemNotFound", "HTTP404", "accessDenied")


def _purge_via_pnp(
    op: Operation, before: dict[str, Any], cfg: Config, site_url: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Shell out to scripts/ps/recycle-purge.ps1.

    Returns ``(after, None)`` on success, ``(None, error_str)`` on
    PS-script failure. Raises ``FileNotFoundError`` if pwsh is not on
    PATH — callers treat that as "fallback unavailable" and revert to
    the manual-instructions message.

    ``after`` always has ``irreversible: True`` — the purge is permanent
    regardless of which code path ran, preserving the invariant that
    ``build_reverse_operation`` raises ``Irreversible`` for any
    ``od-clean(recycle-bin)`` record.
    """
    leaf = before.get("name", "")
    dir_name = normalize_recycle_dir_name(before.get("parent_path", ""))
    # Don't pass -PfxPath: cfg.cert_path is the PEM key, not the PFX that
    # PnP needs. The PS script defaults to ~/.config/fazla-od/fazla-od.pfx
    # (set up per docs/ops/pnp-powershell-setup.md); let that default win.
    code, out, err = invoke_pwsh(_PURGE_PS1, [
        "-Tenant", cfg.tenant_id,
        "-ClientId", cfg.client_id,
        "-SiteUrl", site_url,
        "-LeafName", leaf,
        "-DirName", dir_name,
    ])
    if code != 0:
        msg = (err or out or "").strip()
        return None, msg or f"pwsh exited with code {code}"
    try:
        payload = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError) as e:
        return None, f"could not parse PnP purge JSON: {e}: {out!r}"
    after = {
        "parent_path": "(permanently deleted)",
        "name": payload.get("purged_name", leaf),
        "recycle_bin_item_id": payload.get("recycle_bin_item_id"),
        "irreversible": True,
    }
    return after, None


def purge_recycle_bin_item(
    op: Operation, graph: GraphClient, logger: AuditLogger,
    *, before: dict[str, Any], cfg: Config | None = None,
) -> CleanResult:
    """HARD delete a recycle-bin item. Not reversible."""
    log_mutation_start(logger, op_id=op.op_id, cmd="od-clean(recycle-bin)",
                       args=op.args, drive_id=op.drive_id,
                       item_id=op.item_id, before=before)
    try:
        graph.post_raw(
            f"/drives/{op.drive_id}/items/{op.item_id}/permanentDelete",
            json_body=None,
        )
    except GraphError as e:
        err = str(e)
        if any(t in err for t in _ODFB_PURGE_TOKENS) and cfg is not None:
            # Resolve site URL up front so _purge_via_pnp is trivially
            # testable without a Graph mock. If the lookup itself fails
            # (e.g. unknownLibrarySuffix), fall through to the legacy
            # manual-instructions wrap so operators see why the fallback
            # was skipped.
            try:
                site_url = lookup_site_url_from_drive_id(graph, op.drive_id)
            except GraphError as lookup_exc:
                err = f"{err} | {lookup_exc} | {_ODFB_PURGE_MANUAL}"
                log_mutation_end(logger, op_id=op.op_id, after=None,
                                 result="error", error=err)
                return CleanResult(op_id=op.op_id, status="error", error=err)
            try:
                after, pnp_err = _purge_via_pnp(op, before, cfg, site_url)
            except FileNotFoundError:
                # pwsh not installed — fall back to legacy manual message.
                err = f"{err} | {_ODFB_PURGE_MANUAL}"
                log_mutation_end(logger, op_id=op.op_id, after=None,
                                 result="error", error=err)
                return CleanResult(op_id=op.op_id, status="error", error=err)
            if after is not None:
                log_mutation_end(logger, op_id=op.op_id, after=after,
                                 result="ok")
                return CleanResult(op_id=op.op_id, status="ok", after=after)
            # PS call ran but failed — propagate its stderr as-is.
            log_mutation_end(logger, op_id=op.op_id, after=None,
                             result="error", error=pnp_err)
            return CleanResult(op_id=op.op_id, status="error", error=pnp_err)
        if any(t in err for t in _ODFB_PURGE_TOKENS):
            # ODfB token but no Config — can't run PS. Legacy wrap.
            err = f"{err} | {_ODFB_PURGE_MANUAL}"
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=err)
        return CleanResult(op_id=op.op_id, status="error", error=err)
    after = {"parent_path": "(permanently deleted)",
             "name": before.get("name", ""),
             "irreversible": True}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return CleanResult(op_id=op.op_id, status="ok", after=after)


def remove_old_versions(
    op: Operation, graph: GraphClient, logger: AuditLogger,
    *, before: dict[str, Any],
) -> CleanResult:
    """Keep ``args['keep']`` most-recent versions; delete the rest."""
    keep = int(op.args.get("keep", 3))
    log_mutation_start(logger, op_id=op.op_id, cmd="od-clean(old-versions)",
                       args=op.args, drive_id=op.drive_id,
                       item_id=op.item_id, before=before)
    try:
        body = graph.get(f"/drives/{op.drive_id}/items/{op.item_id}/versions")
        versions = sorted(
            body.get("value", []),
            key=lambda v: _parse_ts(v["lastModifiedDateTime"]),
            reverse=True,
        )
        doomed = versions[keep:]
        deleted_ids: list[str] = []
        for v in doomed:
            graph.delete(
                f"/drives/{op.drive_id}/items/{op.item_id}/versions/{v['id']}"
            )
            deleted_ids.append(v["id"])
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return CleanResult(op_id=op.op_id, status="error", error=str(e))
    after = {"parent_path": before.get("parent_path", ""),
             "name": before.get("name", ""),
             "versions_deleted": deleted_ids,
             "versions_kept": [v["id"] for v in versions[:keep]]}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return CleanResult(op_id=op.op_id, status="ok", after=after)


def revoke_stale_shares(
    op: Operation, graph: GraphClient, logger: AuditLogger,
    *, before: dict[str, Any],
) -> CleanResult:
    """Revoke sharing links older than ``args['older_than_days']``."""
    cutoff_days = int(op.args.get("older_than_days", 90))
    cutoff = datetime.now(timezone.utc) - timedelta(days=cutoff_days)
    log_mutation_start(logger, op_id=op.op_id, cmd="od-clean(stale-shares)",
                       args=op.args, drive_id=op.drive_id,
                       item_id=op.item_id, before=before)
    try:
        body = graph.get(f"/drives/{op.drive_id}/items/{op.item_id}/permissions")
        stale: list[str] = []
        for perm in body.get("value", []):
            link = perm.get("link")
            if not link:
                continue
            created = link.get("createdDateTime")
            if not created:
                continue
            if _parse_ts(created) < cutoff:
                graph.delete(
                    f"/drives/{op.drive_id}/items/{op.item_id}"
                    f"/permissions/{perm['id']}"
                )
                stale.append(perm["id"])
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return CleanResult(op_id=op.op_id, status="error", error=str(e))
    after = {"parent_path": before.get("parent_path", ""),
             "name": before.get("name", ""),
             "permissions_revoked": stale}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return CleanResult(op_id=op.op_id, status="ok", after=after)
