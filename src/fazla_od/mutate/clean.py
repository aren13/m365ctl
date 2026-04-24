"""Specialised cleanup ops: recycle-bin purge, old-versions, stale-shares."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fazla_od.audit import AuditLogger, log_mutation_end, log_mutation_start
from fazla_od.graph import GraphClient, GraphError
from fazla_od.planfile import Operation


@dataclass(frozen=True)
class CleanResult:
    op_id: str
    status: str
    error: str | None = None
    after: dict[str, Any] | None = None


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def purge_recycle_bin_item(
    op: Operation, graph: GraphClient, logger: AuditLogger,
    *, before: dict[str, Any],
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
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return CleanResult(op_id=op.op_id, status="error", error=str(e))
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
