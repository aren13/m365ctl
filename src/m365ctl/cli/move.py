"""`od-move` — move items between parents in OneDrive."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.cli._common import (
    CandidateItem,
    build_graph_client,
    emit_plan,
    expand_pattern,
    new_plan,
    require_plan_for_bulk,
)
from m365ctl.common.config import load_config
from m365ctl.mutate.move import execute_move
from m365ctl.common.planfile import Operation, load_plan, new_op_id
from m365ctl.common.safety import ScopeViolation, assert_scope_allowed, filter_by_scope


def _lookup_item(graph, drive_id: str, item_id: str) -> dict:
    meta = graph.get(f"/drives/{drive_id}/items/{item_id}")
    parent_path = ((meta.get("parentReference") or {}).get("path") or "")
    if parent_path.startswith("/drive/root:"):
        parent_path = parent_path[len("/drive/root:"):] or "/"
    full_path = (
        meta["name"] if parent_path == "/"
        else f"{parent_path}/{meta['name']}"
    )
    return {
        "drive_id": drive_id,
        "item_id": item_id,
        "full_path": full_path,
        "name": meta["name"],
        "parent_path": parent_path,
    }


def run_move(
    *,
    config_path: Path,
    scope: str | None,
    drive_id: str | None,
    item_id: str | None,
    pattern: str | None,
    from_plan: Path | None,
    new_parent_path: str | None,
    new_parent_item_id: str | None,
    plan_out: Path | None,
    confirm: bool,
    unsafe_scope: bool,
) -> int:
    cfg = load_config(config_path)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)

    rc = require_plan_for_bulk(
        pattern=pattern, from_plan=from_plan, confirm=confirm,
        cmd_name="od-move",
    )
    if rc:
        return rc

    if from_plan is not None:
        if not confirm:
            print("od-move --from-plan requires --confirm.", file=sys.stderr)
            return 2
        plan = load_plan(from_plan)
        graph = build_graph_client(cfg, plan.scope)
        any_error = False
        for op in plan.operations:
            if op.action != "move":
                continue
            before_meta = _lookup_item(graph, op.drive_id, op.item_id)
            try:
                assert_scope_allowed(
                    type("X", (), before_meta)(), cfg, unsafe_scope=unsafe_scope
                )
            except ScopeViolation as e:
                print(f"[{op.op_id}] skipped: {e}", file=sys.stderr)
                any_error = True
                continue
            result = execute_move(op, graph, logger,
                                  before={"parent_path": before_meta["parent_path"],
                                          "name": before_meta["name"]})
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    if item_id is not None and drive_id is not None:
        graph = build_graph_client(cfg, scope)
        meta = _lookup_item(graph, drive_id, item_id)
        candidates: list[CandidateItem] = [
            CandidateItem(**meta)
        ]
    elif pattern is not None:
        if scope is None:
            print("od-move --pattern requires --scope", file=sys.stderr)
            return 2
        candidates = list(expand_pattern(cfg, pattern))
    else:
        print(
            "od-move: provide --item-id/--drive-id, --pattern, or --from-plan",
            file=sys.stderr,
        )
        return 2

    if new_parent_item_id is None:
        print("od-move: --new-parent-item-id is required to build a plan",
              file=sys.stderr)
        return 2

    kept = list(filter_by_scope(candidates, cfg, unsafe_scope=unsafe_scope))

    ops = [
        Operation(
            op_id=new_op_id(),
            action="move",
            drive_id=item.drive_id,
            item_id=item.item_id,
            args={
                "new_parent_item_id": new_parent_item_id,
                "new_parent_path": new_parent_path or "",
            },
            dry_run_result=f"would move {item.full_path} -> "
                           f"{new_parent_path or new_parent_item_id}/{item.name}",
        )
        for item in kept
    ]
    src = (
        f"od-move --pattern {pattern!r} --scope {scope}" if pattern
        else f"od-move --item-id {item_id} --drive-id {drive_id}"
    )
    plan = new_plan(source_cmd=src, scope=scope or "", operations=ops)

    if confirm and pattern is None:
        graph = build_graph_client(cfg, scope)
        any_error = False
        for op in plan.operations:
            meta = _lookup_item(graph, op.drive_id, op.item_id)
            result = execute_move(op, graph, logger,
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
    p = argparse.ArgumentParser(prog="od-move")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--scope")
    p.add_argument("--drive-id")
    p.add_argument("--item-id")
    p.add_argument("--pattern")
    p.add_argument("--from-plan", type=Path)
    p.add_argument("--new-parent-path")
    p.add_argument("--new-parent-item-id")
    p.add_argument("--plan-out", type=Path)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--unsafe-scope", action="store_true")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_move(
        config_path=Path(args.config),
        scope=args.scope,
        drive_id=args.drive_id,
        item_id=args.item_id,
        pattern=args.pattern,
        from_plan=args.from_plan,
        new_parent_path=args.new_parent_path,
        new_parent_item_id=args.new_parent_item_id,
        plan_out=args.plan_out,
        confirm=args.confirm,
        unsafe_scope=args.unsafe_scope,
    )
