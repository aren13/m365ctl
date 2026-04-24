"""`od-rename` — rename a single item (or a plan's worth)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.audit import AuditLogger
from m365ctl.cli._common import build_graph_client, emit_plan, new_plan
from m365ctl.cli.move import _lookup_item  # reuse
from m365ctl.config import load_config
from m365ctl.mutate.rename import execute_rename
from m365ctl.planfile import Operation, load_plan, new_op_id
from m365ctl.safety import ScopeViolation, assert_scope_allowed


def run_rename(
    *,
    config_path: Path,
    scope: str | None,
    drive_id: str | None,
    item_id: str | None,
    new_name: str | None,
    from_plan: Path | None,
    plan_out: Path | None,
    confirm: bool,
    unsafe_scope: bool,
) -> int:
    cfg = load_config(config_path)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)

    if from_plan is not None:
        if not confirm:
            print("od-rename --from-plan requires --confirm.", file=sys.stderr)
            return 2
        plan = load_plan(from_plan)
        graph = build_graph_client(cfg, plan.scope)
        any_error = False
        for op in plan.operations:
            if op.action != "rename":
                continue
            meta = _lookup_item(graph, op.drive_id, op.item_id)
            try:
                assert_scope_allowed(type("X", (), meta)(), cfg,
                                     unsafe_scope=unsafe_scope)
            except ScopeViolation as e:
                print(f"[{op.op_id}] skipped: {e}", file=sys.stderr)
                any_error = True
                continue
            result = execute_rename(op, graph, logger,
                                    before={"parent_path": meta["parent_path"],
                                            "name": meta["name"]})
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    if not (item_id and drive_id and new_name):
        print("od-rename: provide --drive-id, --item-id, --new-name (or --from-plan)",
              file=sys.stderr)
        return 2

    graph = build_graph_client(cfg, scope)
    meta = _lookup_item(graph, drive_id, item_id)
    try:
        assert_scope_allowed(type("X", (), meta)(), cfg, unsafe_scope=unsafe_scope)
    except ScopeViolation as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    op = Operation(
        op_id=new_op_id(), action="rename",
        drive_id=drive_id, item_id=item_id,
        args={"new_name": new_name},
        dry_run_result=f"would rename {meta['full_path']} -> "
                       f"{meta['parent_path']}/{new_name}",
    )
    plan = new_plan(
        source_cmd=f"od-rename --drive-id {drive_id} --item-id {item_id} "
                   f"--new-name {new_name!r}",
        scope=scope or "",
        operations=[op],
    )

    if confirm:
        result = execute_rename(op, graph, logger,
                                before={"parent_path": meta["parent_path"],
                                        "name": meta["name"]})
        if result.status != "ok":
            print(f"error: {result.error}", file=sys.stderr)
            return 1
        print(f"[{op.op_id}] ok")
        return 0

    emit_plan(plan, plan_out=plan_out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-rename")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--scope")
    p.add_argument("--drive-id")
    p.add_argument("--item-id")
    p.add_argument("--new-name")
    p.add_argument("--from-plan", type=Path)
    p.add_argument("--plan-out", type=Path)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--unsafe-scope", action="store_true")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_rename(
        config_path=Path(args.config),
        scope=args.scope, drive_id=args.drive_id,
        item_id=args.item_id, new_name=args.new_name,
        from_plan=args.from_plan, plan_out=args.plan_out,
        confirm=args.confirm, unsafe_scope=args.unsafe_scope,
    )
