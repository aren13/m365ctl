"""Build a bulk-move plan that lands old messages into Archive/<YYYY>/<MM>.

This convenience module is a pure data → Plan transformation. The CLI fetches
catalog rows, calls :func:`build_archive_plan`, and either writes the plan to
disk (``--plan-out``) or dispatches each op via the existing Phase 10 executor
table (``--confirm``).

No new audit-action namespaces — every op uses ``mail.move``, so audit + undo
flow through the existing surface.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from m365ctl.common.planfile import PLAN_SCHEMA_VERSION, Operation, Plan, new_op_id


def build_archive_plan(
    rows: list[dict[str, Any]],
    *,
    older_than_days: int,
    folder: str,
    mailbox_upn: str,
    source_cmd: str,
    scope: str,
    now: datetime,
) -> Plan:
    """Emit a Plan of ``mail.move`` ops for messages older than the cutoff.

    Each qualifying row produces one ``mail.move`` op whose ``to_folder`` is
    ``Archive/<YYYY>/<MM>`` derived from that row's ``received_at``. Rows that
    live in a different folder, lack a ``received_at``, or are newer than the
    cutoff are skipped.
    """
    cutoff = now - timedelta(days=older_than_days)
    ops: list[Operation] = []
    for r in rows:
        path = r.get("parent_folder_path") or ""
        if path != folder:
            continue
        received = r.get("received_at")
        if received is None:
            continue
        if isinstance(received, str):
            received = datetime.fromisoformat(received.replace("Z", "+00:00"))
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        if received >= cutoff:
            continue
        rule_name = f"mail-archive-{received:%Y%m}"
        target = f"Archive/{received:%Y}/{received:%m}"
        ops.append(Operation(
            op_id=new_op_id(),
            action="mail.move",
            drive_id=mailbox_upn,
            item_id=r["message_id"],
            args={"rule_name": rule_name, "to_folder": target},
            dry_run_result=(
                f"[{rule_name}] would move -> {target}: "
                f"{r.get('from_address')} | {(r.get('subject') or '')[:60]}"
            ),
        ))
    return Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at=now.isoformat(),
        source_cmd=source_cmd,
        scope=scope,
        operations=ops,
    )


__all__ = ["build_archive_plan"]
