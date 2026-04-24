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
from dataclasses import dataclass
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

_RESTORE_PS1 = PS_SCRIPTS_DIR / "recycle-restore.ps1"


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

# Graph error codes that trigger the PnP.PowerShell fallback. The ODfB
# /restore endpoint surfaces as different codes depending on tenant
# config: 'notSupported' is the documented response, 'invalidRequest' is
# the typical one for this endpoint on Business tenants, and 'BadRequest'
# covers the older response style and the case where the Graph SDK
# returns an HTTP 400 without parsing out a specific error code. If this
# tuple ever proves too broad (e.g. masking a legit BadRequest unrelated
# to recycle-bin restore), narrow it to the first two tokens.
_ODFB_RESTORE_TOKENS = ("notSupported", "BadRequest", "invalidRequest")


def _restore_via_pnp(
    op: Operation, before: dict[str, Any], cfg: Config, site_url: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Shell out to scripts/ps/recycle-restore.ps1.

    Returns ``(after, None)`` on success, ``(None, error_str)`` on
    PS-script failure. Raises ``FileNotFoundError`` if pwsh is not on
    PATH — callers treat that as "fallback unavailable" and revert to
    the manual-instructions message.
    """
    leaf = before.get("name", "")
    dir_name = normalize_recycle_dir_name(before.get("parent_path", ""))
    # Don't pass -PfxPath: cfg.cert_path is the PEM key, not the PFX that
    # PnP needs. The PS script defaults to ~/.config/fazla-od/fazla-od.pfx
    # (set up per docs/ops/pnp-powershell-setup.md); let that default win.
    code, out, err = invoke_pwsh(_RESTORE_PS1, [
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
        return None, f"could not parse PnP restore JSON: {e}: {out!r}"
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
            # Resolve site URL up front so _restore_via_pnp is trivially
            # testable without a Graph mock. If the lookup itself fails
            # (e.g. unknownLibrarySuffix), fall through to the legacy
            # manual-instructions wrap so operators see why the fallback
            # was skipped.
            try:
                site_url = lookup_site_url_from_drive_id(graph, op.drive_id)
            except GraphError as lookup_exc:
                err = f"{err} | {lookup_exc} | {_ODFB_RESTORE_MANUAL}"
                log_mutation_end(logger, op_id=op.op_id, after=None,
                                 result="error", error=err)
                return DeleteResult(op_id=op.op_id, status="error", error=err)
            try:
                after, pnp_err = _restore_via_pnp(op, before, cfg, site_url)
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
