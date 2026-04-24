"""Shared helpers for mutating CLIs.

- ``build_graph_client``: config -> GraphClient (picks credential by scope)
- ``expand_pattern``: fnmatch against the catalog to produce candidate items
- ``require_plan_for_bulk``: spec §7 rule 2 guard
- ``emit_plan_or_tsv``: dry-run output
"""
from __future__ import annotations

import fnmatch
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from m365ctl.common.auth import AppOnlyCredential, DelegatedCredential
from m365ctl.onedrive.catalog.db import open_catalog
from m365ctl.common.config import Config
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import PLAN_SCHEMA_VERSION, Operation, Plan, write_plan


@dataclass(frozen=True)
class CandidateItem:
    drive_id: str
    item_id: str
    full_path: str
    name: str
    parent_path: str


def build_graph_client(cfg: Config, scope: str | None) -> GraphClient:
    cred = (
        DelegatedCredential(cfg) if scope == "me"
        else AppOnlyCredential(cfg)
    )
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    _expand_me_in_allow_drives(cfg, graph, scope)
    return graph


def _expand_me_in_allow_drives(
    cfg: Config, graph: GraphClient, scope: str | None
) -> None:
    """Resolve the ``"me"`` sentinel in ``cfg.scope.allow_drives`` to the
    delegated user's actual drive id.

    Users commonly configure ``allow_drives = ["me"]``, but ``safety.py``
    compares against real drive ids, so the literal ``"me"`` would never
    match. This expansion is idempotent: if already resolved (no ``"me"``
    in the list), it's a no-op. App-only scopes have no delegated user,
    so we leave ``"me"`` in place as a harmless no-op sentinel there.
    """
    allow = cfg.scope.allow_drives
    if "me" not in allow:
        return
    if scope != "me":
        # Only expand when the delegated credential is actually in use.
        # App-only token has no "/me/drive".
        return
    try:
        me_drive = graph.get("/me/drive")
    except GraphError:
        return  # leave as no-op sentinel
    real_id = (me_drive or {}).get("id")
    if not real_id:
        return
    idx = allow.index("me")
    allow[idx] = real_id


def expand_pattern(
    cfg: Config,
    pattern: str,
    scope_drive_ids: list[str] | None = None,
) -> Iterator[CandidateItem]:
    """Match ``pattern`` (fnmatch) against item full_paths in the catalog."""
    with open_catalog(cfg.catalog.path) as conn:
        where = "is_folder = false AND is_deleted = false"
        params: list = []
        if scope_drive_ids:
            where += " AND drive_id = ANY(?)"
            params.append(scope_drive_ids)
        cur = conn.execute(
            f"SELECT drive_id, item_id, full_path, name, parent_path "
            f"FROM items WHERE {where}",
            params,
        )
        for drive_id, item_id, full_path, name, parent_path in cur.fetchall():
            if fnmatch.fnmatch(full_path, pattern):
                yield CandidateItem(drive_id, item_id, full_path, name,
                                    parent_path)


def require_plan_for_bulk(
    *, pattern: str | None, from_plan: Path | None,
    confirm: bool, cmd_name: str,
) -> int:
    """Spec §7 rule 2: patterns + --confirm without --from-plan is rejected."""
    if pattern is not None and confirm and not from_plan:
        print(
            f"{cmd_name}: bulk selection ({pattern!r}) requires the plan-file "
            "workflow. Generate a plan with --plan-out, review it, then "
            "execute with --from-plan --confirm.",
            file=sys.stderr,
        )
        return 2
    return 0


def emit_plan(
    plan: Plan,
    *,
    plan_out: Path | None,
) -> None:
    if plan_out is not None:
        write_plan(plan, plan_out)
        print(f"Wrote plan: {plan_out}  ({len(plan.operations)} ops)")
        return
    print("DRY-RUN — no mutations applied. Re-run with --plan-out for full JSON.")
    print("op_id\taction\tdrive_id\titem_id\tdry_run_result")
    for op in plan.operations:
        print(f"{op.op_id}\t{op.action}\t{op.drive_id}\t"
              f"{op.item_id}\t{op.dry_run_result}")


def new_plan(*, source_cmd: str, scope: str,
             operations: list[Operation]) -> Plan:
    return Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(),
        source_cmd=source_cmd,
        scope=scope,
        operations=operations,
    )
