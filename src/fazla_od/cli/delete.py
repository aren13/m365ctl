"""`od-delete` — recycle-bin delete (soft). Hard delete is od-clean."""
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
from fazla_od.cli.move import _lookup_item  # reuse
from fazla_od.config import load_config
from fazla_od.mutate.delete import execute_recycle_delete
from fazla_od.planfile import Operation, load_plan, new_op_id
from fazla_od.safety import ScopeViolation, assert_scope_allowed, filter_by_scope


def run_delete(
    *,
    config_path: Path,
    scope: str | None,
    drive_id: str | None,
    item_id: str | None,
    pattern: str | None,
    from_plan: Path | None,
    plan_out: Path | None,
    confirm: bool,
    unsafe_scope: bool,
) -> int:
    cfg = load_config(config_path)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)

    rc = require_plan_for_bulk(
        pattern=pattern, from_plan=from_plan, confirm=confirm,
        cmd_name="od-delete",
    )
    if rc:
        return rc

    if from_plan is not None:
        if not confirm:
            print("od-delete --from-plan requires --confirm.", file=sys.stderr)
            return 2
        plan = load_plan(from_plan)
        graph = build_graph_client(cfg, plan.scope)
        any_error = False
        for op in plan.operations:
            if op.action != "delete":
                continue
            meta = _lookup_item(graph, op.drive_id, op.item_id)
            try:
                assert_scope_allowed(type("X", (), meta)(), cfg,
                                     unsafe_scope=unsafe_scope)
            except ScopeViolation as e:
                print(f"[{op.op_id}] skipped: {e}", file=sys.stderr)
                any_error = True
                continue
            result = execute_recycle_delete(
                op, graph, logger,
                before={"parent_path": meta["parent_path"], "name": meta["name"]},
            )
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok (recycled)")
        return 1 if any_error else 0

    if item_id is not None and drive_id is not None:
        graph = build_graph_client(cfg, scope)
        meta = _lookup_item(graph, drive_id, item_id)
        candidates: list[CandidateItem] = [CandidateItem(**meta)]
    elif pattern is not None:
        if scope is None:
            print("od-delete --pattern requires --scope", file=sys.stderr)
            return 2
        candidates = list(expand_pattern(cfg, pattern))
    else:
        print(
            "od-delete: provide --item-id/--drive-id, --pattern, or --from-plan",
            file=sys.stderr,
        )
        return 2

    kept = list(filter_by_scope(candidates, cfg, unsafe_scope=unsafe_scope))

    ops = [
        Operation(
            op_id=new_op_id(),
            action="delete",
            drive_id=item.drive_id,
            item_id=item.item_id,
            args={},
            dry_run_result=f"would recycle {item.full_path}",
        )
        for item in kept
    ]
    src = (
        f"od-delete --pattern {pattern!r} --scope {scope}" if pattern
        else f"od-delete --item-id {item_id} --drive-id {drive_id}"
    )
    plan = new_plan(source_cmd=src, scope=scope or "", operations=ops)

    if confirm and pattern is None:
        graph = build_graph_client(cfg, scope)
        any_error = False
        for op in plan.operations:
            meta = _lookup_item(graph, op.drive_id, op.item_id)
            result = execute_recycle_delete(
                op, graph, logger,
                before={"parent_path": meta["parent_path"], "name": meta["name"]},
            )
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok (recycled)")
        return 1 if any_error else 0

    emit_plan(plan, plan_out=plan_out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-delete")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--scope")
    p.add_argument("--drive-id")
    p.add_argument("--item-id")
    p.add_argument("--pattern")
    p.add_argument("--from-plan", type=Path)
    p.add_argument("--plan-out", type=Path)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--unsafe-scope", action="store_true")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_delete(
        config_path=Path(args.config),
        scope=args.scope, drive_id=args.drive_id, item_id=args.item_id,
        pattern=args.pattern, from_plan=args.from_plan,
        plan_out=args.plan_out, confirm=args.confirm,
        unsafe_scope=args.unsafe_scope,
    )
