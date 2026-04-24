"""`m365ctl mail categories [list|add|update|remove|sync]` — reader + CRUD."""
from __future__ import annotations

import argparse
import sys

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.categories import list_master_categories
from m365ctl.mail.cli._common import (
    add_common_args,
    emit_json_lines,
    load_and_authorize,
)
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.categories import (
    compute_sync_plan,
    execute_add_category,
    execute_remove_category,
    execute_update_category,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail categories")
    add_common_args(p)
    sub = p.add_subparsers(dest="subcommand", required=False)

    sub.add_parser("list", help="List master categories (default).")

    a = sub.add_parser("add", help="Add a master category.")
    a.add_argument("name")
    a.add_argument("--color", default="preset0", help="preset0..preset24 (default: preset0)")
    a.add_argument("--confirm", action="store_true")

    u = sub.add_parser("update", help="Update a master category.")
    u.add_argument("id")
    u.add_argument("--name", help="New display name.")
    u.add_argument("--color", help="New color (preset0..preset24).")
    u.add_argument("--confirm", action="store_true")

    rm = sub.add_parser("remove", help="Remove a master category.")
    rm.add_argument("id")
    rm.add_argument("--confirm", action="store_true")

    s = sub.add_parser("sync", help="Reconcile categories_master from config.")
    s.add_argument("--confirm", action="store_true")
    return p


def _build_logger(cfg) -> AuditLogger:
    return AuditLogger(ops_dir=cfg.logging.ops_dir)


def _run_list(args) -> int:
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    cats = list_master_categories(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)

    if args.json:
        emit_json_lines(cats)
    else:
        for c in cats:
            print(f"{c.color:<12}  {c.display_name}  (id: {c.id})")
    return 0


def _run_add(args) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        print(f"(dry-run) would add category {args.name!r} color={args.color!r}", file=sys.stderr)
        return 0
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    op = Operation(
        op_id=new_op_id(), action="mail.categories.add",
        drive_id=derive_mailbox_upn(args.mailbox), item_id="",
        args={"name": args.name, "color": args.color, "auth_mode": auth_mode},
    )
    result = execute_add_category(op, graph, _build_logger(cfg), before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — added {args.name!r}")
    return 0


def _run_update(args) -> int:
    if args.name is None and args.color is None:
        print("mail categories update: pass --name or --color (or both)", file=sys.stderr)
        return 2
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        patch = {}
        if args.name is not None:
            patch["name"] = args.name
        if args.color is not None:
            patch["color"] = args.color
        print(f"(dry-run) would update category {args.id} with {patch}", file=sys.stderr)
        return 0
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    current = list_master_categories(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)
    before = next(
        ({"display_name": c.display_name, "color": c.color} for c in current if c.id == args.id),
        {},
    )
    call_args: dict = {"auth_mode": auth_mode}
    if args.name is not None:
        call_args["name"] = args.name
    if args.color is not None:
        call_args["color"] = args.color
    op = Operation(
        op_id=new_op_id(), action="mail.categories.update",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.id,
        args=call_args,
    )
    result = execute_update_category(op, graph, _build_logger(cfg), before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — updated {args.id}")
    return 0


def _run_remove(args) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        print(f"(dry-run) would remove category {args.id}", file=sys.stderr)
        return 0
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    current = list_master_categories(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)
    before = next(
        ({"display_name": c.display_name, "color": c.color} for c in current if c.id == args.id),
        {},
    )
    op = Operation(
        op_id=new_op_id(), action="mail.categories.remove",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.id,
        args={"auth_mode": auth_mode},
    )
    result = execute_remove_category(op, graph, _build_logger(cfg), before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — removed {args.id}")
    return 0


def _run_sync(args) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    live = list_master_categories(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)
    desired = list(cfg.mail.categories_master)
    plan = compute_sync_plan(live, desired)
    if not plan:
        print("mail categories sync: already in sync — nothing to do.")
        return 0
    if not args.confirm:
        for op_spec in plan:
            print(f"(dry-run) would add category {op_spec['args']['name']!r}")
        print(f"(dry-run) {len(plan)} categories to add (use --confirm to execute).",
              file=sys.stderr)
        return 0
    logger = _build_logger(cfg)
    any_error = False
    for op_spec in plan:
        op_spec["args"]["auth_mode"] = auth_mode
        op = Operation(**op_spec)
        result = execute_add_category(op, graph, logger, before={})
        if result.status != "ok":
            any_error = True
            print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
        else:
            print(f"[{op.op_id}] ok — added {op.args['name']!r}")
    return 1 if any_error else 0


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.subcommand in (None, "list"):
        return _run_list(args)
    if args.subcommand == "add":
        return _run_add(args)
    if args.subcommand == "update":
        return _run_update(args)
    if args.subcommand == "remove":
        return _run_remove(args)
    if args.subcommand == "sync":
        return _run_sync(args)
    return 2
