"""`od-clean` — cleanup ops: recycle-bin, old-versions, stale-shares."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fazla_od.audit import AuditLogger
from fazla_od.cli._common import (
    CandidateItem,
    build_graph_client,
    emit_plan,
    expand_pattern,
    new_plan,
    require_plan_for_bulk,
)
from fazla_od.cli.move import _lookup_item
from fazla_od.config import load_config
from fazla_od.mutate.clean import (
    purge_recycle_bin_item,
    remove_old_versions,
    revoke_stale_shares,
)
from fazla_od.planfile import Operation, load_plan, new_op_id
from fazla_od.safety import ScopeViolation, assert_scope_allowed, filter_by_scope


_ACTION_EXECUTORS = {
    "recycle-purge": purge_recycle_bin_item,
    "version-delete": remove_old_versions,
    "share-revoke": revoke_stale_shares,
}

_SUBCOMMAND_ACTIONS = {
    "recycle-bin": "recycle-purge",
    "old-versions": "version-delete",
    "stale-shares": "share-revoke",
}


def run_clean(
    *,
    config_path: Path,
    subcmd: str,
    scope: str | None,
    drive_id: str | None,
    item_id: str | None,
    pattern: str | None,
    from_plan: Path | None,
    plan_out: Path | None,
    keep: int | None,
    older_than_days: int | None,
    confirm: bool,
    unsafe_scope: bool,
) -> int:
    cfg = load_config(config_path)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    action = _SUBCOMMAND_ACTIONS.get(subcmd)
    if action is None:
        print(f"od-clean: unknown subcommand {subcmd!r}", file=sys.stderr)
        return 2

    rc = require_plan_for_bulk(
        pattern=pattern, from_plan=from_plan, confirm=confirm,
        cmd_name=f"od-clean {subcmd}",
    )
    if rc:
        return rc

    # --from-plan execute path
    if from_plan is not None:
        if not confirm:
            print("od-clean --from-plan requires --confirm.", file=sys.stderr)
            return 2
        plan = load_plan(from_plan)
        graph = build_graph_client(cfg, plan.scope)
        any_error = False
        for op in plan.operations:
            if op.action != action:
                continue
            meta = _lookup_item(graph, op.drive_id, op.item_id)
            try:
                assert_scope_allowed(type("X", (), meta)(), cfg,
                                     unsafe_scope=unsafe_scope)
            except ScopeViolation as e:
                print(f"[{op.op_id}] skipped: {e}", file=sys.stderr)
                any_error = True
                continue
            exec_fn = _ACTION_EXECUTORS[action]
            result = exec_fn(op, graph, logger,
                             before={"parent_path": meta["parent_path"],
                                     "name": meta["name"]})
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    # Selection
    if item_id is not None and drive_id is not None:
        graph = build_graph_client(cfg, scope)
        meta = _lookup_item(graph, drive_id, item_id)
        candidates: list[CandidateItem] = [CandidateItem(**meta)]
    elif pattern is not None:
        if scope is None:
            print(f"od-clean {subcmd} --pattern requires --scope", file=sys.stderr)
            return 2
        candidates = list(expand_pattern(cfg, pattern))
    else:
        print(
            f"od-clean {subcmd}: provide --item-id/--drive-id, --pattern, or --from-plan",
            file=sys.stderr,
        )
        return 2

    kept = list(filter_by_scope(candidates, cfg, unsafe_scope=unsafe_scope))

    args_payload: dict = {}
    if action == "version-delete":
        args_payload["keep"] = int(keep) if keep is not None else 3
    elif action == "share-revoke":
        args_payload["older_than_days"] = (
            int(older_than_days) if older_than_days is not None else 90
        )

    ops = [
        Operation(
            op_id=new_op_id(),
            action=action,
            drive_id=item.drive_id,
            item_id=item.item_id,
            args=dict(args_payload),
            dry_run_result=(
                f"would permanently delete {item.full_path}" if action == "recycle-purge"
                else f"would prune versions on {item.full_path} (keep={args_payload.get('keep', 3)})"
                if action == "version-delete"
                else f"would revoke shares older than "
                     f"{args_payload.get('older_than_days', 90)}d on {item.full_path}"
            ),
        )
        for item in kept
    ]
    plan = new_plan(
        source_cmd=f"od-clean {subcmd} --pattern {pattern!r}" if pattern
                   else f"od-clean {subcmd} --drive-id {drive_id} --item-id {item_id}",
        scope=scope or "",
        operations=ops,
    )

    if confirm and pattern is None:
        graph = build_graph_client(cfg, scope)
        any_error = False
        exec_fn = _ACTION_EXECUTORS[action]
        for op in plan.operations:
            meta = _lookup_item(graph, op.drive_id, op.item_id)
            result = exec_fn(op, graph, logger,
                             before={"parent_path": meta["parent_path"],
                                     "name": meta["name"]})
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    emit_plan(plan, plan_out=plan_out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-clean")
    sub = p.add_subparsers(dest="subcmd", required=True)
    for name in ("recycle-bin", "old-versions", "stale-shares"):
        sp = sub.add_parser(name)
        sp.add_argument("--config", default="config.toml")
        sp.add_argument("--scope")
        sp.add_argument("--drive-id")
        sp.add_argument("--item-id")
        sp.add_argument("--pattern")
        sp.add_argument("--from-plan", type=Path)
        sp.add_argument("--plan-out", type=Path)
        sp.add_argument("--confirm", action="store_true")
        sp.add_argument("--unsafe-scope", action="store_true")
        if name == "old-versions":
            sp.add_argument("--keep", type=int, default=3)
        if name == "stale-shares":
            sp.add_argument("--older-than-days", type=int, default=90,
                            dest="older_than_days")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_clean(
        config_path=Path(args.config),
        subcmd=args.subcmd,
        scope=args.scope, drive_id=args.drive_id, item_id=args.item_id,
        pattern=args.pattern, from_plan=args.from_plan,
        plan_out=args.plan_out,
        keep=getattr(args, "keep", None),
        older_than_days=getattr(args, "older_than_days", None),
        confirm=args.confirm, unsafe_scope=args.unsafe_scope,
    )
