"""`od-undo <op_id>` — replay a reverse-op from the audit log."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.onedrive.cli._common import build_graph_client
from m365ctl.onedrive.cli.label import _lookup_label_item
from m365ctl.onedrive.cli.move import _lookup_item
from m365ctl.common.config import load_config
from m365ctl.common.graph import GraphError
from m365ctl.onedrive.mutate.delete import execute_recycle_delete, execute_restore
from m365ctl.onedrive.mutate.label import execute_label_apply, execute_label_remove
from m365ctl.onedrive.mutate.move import execute_move
from m365ctl.onedrive.mutate.rename import execute_rename
from m365ctl.onedrive.mutate.undo import (
    Irreversible,
    build_reverse_operation,
    register_od_inverses,
)
from m365ctl.common.safety import ScopeViolation, assert_scope_allowed
from m365ctl.common.undo import (
    Dispatcher,
    IrreversibleOp,
    UnknownAction,
    normalize_legacy_action,
)


_DISPATCHER = Dispatcher()
register_od_inverses(_DISPATCHER)


def run_undo(*, config_path: Path, op_id: str, confirm: bool,
             unsafe_scope: bool) -> int:
    cfg = load_config(config_path)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    try:
        rev = build_reverse_operation(logger, op_id)
    except Irreversible as e:
        print(f"irreversible: {e}", file=sys.stderr)
        return 2

    # Preflight via the domain-agnostic Dispatcher: normalizes bare legacy
    # actions (`"move"` → `"od.move"`) and rejects unknown/irreversible
    # verbs with a clean error before we spin up a Graph client.
    action_full = normalize_legacy_action(rev.action)
    if not _DISPATCHER.is_registered(action_full):
        print(f"undo: no inverse for action {action_full!r}", file=sys.stderr)
        return 2
    # Suffix after the `<domain>.` prefix — used for the manual executor
    # switch below so it stays tolerant of both legacy bare and namespaced
    # forms.
    suffix = action_full.split(".", 1)[1] if "." in action_full else action_full

    print(f"Reverse op: {rev.action} — {rev.dry_run_result}")
    if not confirm:
        print("DRY-RUN — pass --confirm to execute.")
        return 0

    graph = build_graph_client(cfg, scope=None)

    use_label_lookup = suffix in ("label-apply", "label-remove")
    lookup_fn = _lookup_label_item if use_label_lookup else _lookup_item
    try:
        before = lookup_fn(graph, rev.drive_id, rev.item_id)
    except GraphError:
        # restore-from-recycle and label-remove can legitimately 404 here; proceed
        # with minimal metadata. Transient/auth errors propagate via this too, but
        # they'd surface immediately at execute time.
        before = {"parent_path": "(unknown)", "name": ""}
        if use_label_lookup:
            # execute_label_* needs this key even if empty.
            before["server_relative_url"] = ""

    scope_probe = {"drive_id": rev.drive_id, "item_id": rev.item_id,
                   "full_path": before.get("parent_path", "") + "/" + before.get("name", ""),
                   "name": before.get("name", ""),
                   "parent_path": before.get("parent_path", "")}
    try:
        assert_scope_allowed(type("X", (), scope_probe)(), cfg, unsafe_scope=unsafe_scope)
    except ScopeViolation as e:
        print(f"scope violation: {e}", file=sys.stderr)
        return 2

    if suffix == "rename":
        r = execute_rename(rev, graph, logger, before=before)
    elif suffix == "move":
        r = execute_move(rev, graph, logger, before=before)
    elif suffix == "delete":
        r = execute_recycle_delete(rev, graph, logger, before=before)
    elif suffix == "restore":
        # The item is in the recycle bin — the live `_lookup_item` above
        # 404s and `before` is the {"parent_path": "(unknown)", "name": ""}
        # fallback. Prefer the delete op's recorded `before` (threaded
        # through args by `build_reverse_operation`). The `or`-fallback
        # keeps compatibility with audit records produced before this fix.
        restore_before = {
            "name": rev.args.get("orig_name") or before.get("name", ""),
            "parent_path": (rev.args.get("orig_parent_path")
                            or before.get("parent_path", "")),
        }
        r = execute_restore(rev, graph, logger, before=restore_before, cfg=cfg)
    elif suffix == "label-apply":
        r = execute_label_apply(rev, logger, before=before)
    elif suffix == "label-remove":
        r = execute_label_remove(rev, logger, before=before)
    else:
        print(f"no executor wired for reverse action {rev.action!r}",
              file=sys.stderr)
        return 2

    if r.status != "ok":
        print(f"undo failed: {r.error}", file=sys.stderr)
        return 1
    print(f"[{rev.op_id}] ok (reverse of {op_id})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-undo")
    p.add_argument("op_id")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--unsafe-scope", action="store_true")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_undo(config_path=Path(args.config), op_id=args.op_id,
                    confirm=args.confirm, unsafe_scope=args.unsafe_scope)
