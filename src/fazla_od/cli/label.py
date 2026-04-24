"""`od-label` — apply/remove sensitivity labels via PnP.PowerShell."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fazla_od.audit import AuditLogger
from fazla_od.cli._common import build_graph_client, emit_plan, new_plan
from fazla_od.config import load_config
from fazla_od.mutate.label import execute_label_apply, execute_label_remove
from fazla_od.planfile import Operation, load_plan, new_op_id
from fazla_od.safety import ScopeViolation, assert_scope_allowed


_ACTION_EXECUTORS = {
    "label-apply": execute_label_apply,
    "label-remove": execute_label_remove,
}


def _lookup_label_item(graph, drive_id: str, item_id: str) -> dict:
    """Like cli.move._lookup_item but also includes server_relative_url
    (derived from the parent path + name) for PnP cmdlets."""
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
        # Without reliable SharePoint context, use full_path as a stand-in.
        # Real live use needs callers to pass --site-url and provide the true
        # server-relative path; for single-item the tool can accept
        # --server-relative-url as override, but default to full_path.
        "server_relative_url": full_path,
    }


def run_label(
    *,
    config_path: Path,
    subcmd: str,
    scope: str | None,
    drive_id: str | None,
    item_id: str | None,
    label: str | None,
    site_url: str | None,
    server_relative_url: str | None,
    from_plan: Path | None,
    plan_out: Path | None,
    confirm: bool,
    unsafe_scope: bool,
) -> int:
    cfg = load_config(config_path)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)

    action = "label-apply" if subcmd == "apply" else "label-remove"

    if from_plan is not None:
        if not confirm:
            print("od-label --from-plan requires --confirm.", file=sys.stderr)
            return 2
        plan = load_plan(from_plan)
        graph = build_graph_client(cfg, plan.scope)
        any_error = False
        for op in plan.operations:
            if op.action != action:
                continue
            meta = _lookup_label_item(graph, op.drive_id, op.item_id)
            try:
                assert_scope_allowed(type("X", (), meta)(), cfg,
                                     unsafe_scope=unsafe_scope)
            except ScopeViolation as e:
                print(f"[{op.op_id}] skipped: {e}", file=sys.stderr)
                any_error = True
                continue
            exec_fn = _ACTION_EXECUTORS[action]
            result = exec_fn(op, logger,
                             before={"parent_path": meta["parent_path"],
                                     "name": meta["name"],
                                     "server_relative_url":
                                         meta["server_relative_url"]})
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    if not (drive_id and item_id and site_url):
        print("od-label: provide --drive-id, --item-id, --site-url (or --from-plan)",
              file=sys.stderr)
        return 2
    if action == "label-apply" and not label:
        print("od-label apply: --label is required", file=sys.stderr)
        return 2

    graph = build_graph_client(cfg, scope)
    meta = _lookup_label_item(graph, drive_id, item_id)
    if server_relative_url:
        meta["server_relative_url"] = server_relative_url
    try:
        assert_scope_allowed(type("X", (), meta)(), cfg, unsafe_scope=unsafe_scope)
    except ScopeViolation as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    args_payload: dict = {"site_url": site_url}
    if action == "label-apply":
        args_payload["label"] = label
    op = Operation(
        op_id=new_op_id(), action=action,
        drive_id=drive_id, item_id=item_id,
        args=args_payload,
        dry_run_result=(
            f"would apply label {label!r} to {meta['full_path']}"
            if action == "label-apply"
            else f"would remove label from {meta['full_path']}"
        ),
    )
    plan = new_plan(
        source_cmd=f"od-label {subcmd} --drive-id {drive_id} --item-id {item_id}",
        scope=scope or "",
        operations=[op],
    )

    if confirm:
        exec_fn = _ACTION_EXECUTORS[action]
        result = exec_fn(op, logger,
                        before={"parent_path": meta["parent_path"],
                                "name": meta["name"],
                                "server_relative_url": meta["server_relative_url"]})
        if result.status != "ok":
            print(f"error: {result.error}", file=sys.stderr)
            return 1
        print(f"[{op.op_id}] ok")
        return 0

    emit_plan(plan, plan_out=plan_out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-label")
    sub = p.add_subparsers(dest="subcmd", required=True)
    for name in ("apply", "remove"):
        sp = sub.add_parser(name)
        sp.add_argument("--config", default="config.toml")
        sp.add_argument("--scope")
        sp.add_argument("--drive-id")
        sp.add_argument("--item-id")
        sp.add_argument("--site-url")
        sp.add_argument("--server-relative-url")
        sp.add_argument("--from-plan", type=Path)
        sp.add_argument("--plan-out", type=Path)
        sp.add_argument("--confirm", action="store_true")
        sp.add_argument("--unsafe-scope", action="store_true")
        if name == "apply":
            sp.add_argument("--label")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_label(
        config_path=Path(args.config),
        subcmd=args.subcmd,
        scope=args.scope, drive_id=args.drive_id, item_id=args.item_id,
        label=getattr(args, "label", None),
        site_url=args.site_url,
        server_relative_url=args.server_relative_url,
        from_plan=args.from_plan, plan_out=args.plan_out,
        confirm=args.confirm, unsafe_scope=args.unsafe_scope,
    )
