"""`m365ctl mail categories [list|add|update|remove|sync]` — reader + CRUD."""
from __future__ import annotations

import argparse
import sys
from urllib.parse import quote

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.categories import list_master_categories
from m365ctl.mail.cli._bulk import execute_plan_in_batches
from m365ctl.mail.cli._common import (
    add_common_args,
    emit_json_lines,
    load_and_authorize,
)
from m365ctl.mail.endpoints import user_base, user_base_for_op
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.categories import (
    compute_sync_plan,
    execute_add_category,
    execute_remove_category,
    execute_update_category,
)
from m365ctl.mail.mutate.categorize import finish_categorize, start_categorize


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
    rm.add_argument(
        "--strip-from-messages",
        dest="strip_from_messages",
        action="store_true",
        help=(
            "Before deleting the master record, find every message tagged with "
            "this category name and PATCH it to remove the tag. Each per-message "
            "strip is its own audit op (undoable individually via `m365ctl undo`)."
        ),
    )
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


def _list_messages_tagged_with(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: str,
    name: str,
) -> list[tuple[str, list[str]]]:
    """Return ``(message_id, current_categories)`` for every message tagged ``name``.

    Walks ``@odata.nextLink`` pagination. Used by ``mail categories remove
    --strip-from-messages``.
    """
    ub = user_base(mailbox_spec, auth_mode=auth_mode)  # type: ignore[arg-type]
    # Graph OData: single quotes inside string literals are escaped by doubling.
    escaped = name.replace("'", "''")
    filter_clause = f"categories/any(c:c eq '{escaped}')"
    path = (
        f"{ub}/messages"
        f"?$filter={quote(filter_clause, safe='')}"
        "&$select=id,categories"
        "&$top=999"
    )
    out: list[tuple[str, list[str]]] = []
    body = graph.get(path)
    while True:
        for raw in body.get("value", []) or []:
            mid = raw.get("id")
            if not mid:
                continue
            out.append((mid, list(raw.get("categories", []) or [])))
        next_link = body.get("@odata.nextLink")
        if not next_link:
            break
        body = graph.get_absolute(next_link)
    return out


def _run_remove(args) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        if args.strip_from_messages:
            print(
                f"(dry-run) would strip category from messages, then remove "
                f"category {args.id}",
                file=sys.stderr,
            )
        else:
            print(f"(dry-run) would remove category {args.id}", file=sys.stderr)
        return 0
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    current = list_master_categories(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)
    target = next((c for c in current if c.id == args.id), None)
    before = (
        {"display_name": target.display_name, "color": target.color}
        if target is not None
        else {}
    )
    logger = _build_logger(cfg)

    if args.strip_from_messages:
        if target is None:
            print(
                f"mail categories remove: category id {args.id!r} not found; "
                f"cannot resolve display name to strip from messages.",
                file=sys.stderr,
            )
            return 2
        name = target.display_name
        tagged = _list_messages_tagged_with(
            graph, mailbox_spec=args.mailbox, auth_mode=auth_mode, name=name,
        )
        if tagged:
            strip_ops = [
                Operation(
                    op_id=new_op_id(),
                    action="mail.categorize",
                    drive_id=derive_mailbox_upn(args.mailbox),
                    item_id=mid,
                    args={
                        "categories": [c for c in cats if c != name],
                        "auth_mode": auth_mode,
                    },
                )
                for mid, cats in tagged
            ]

            def fetch_before(b, op):
                ub = user_base_for_op(op)
                return b.get(f"{ub}/messages/{op.item_id}?$select=id,categories")

            def parse_before(op, body, err):
                if not body:
                    return {}
                return {"categories": list(body.get("categories", []))}

            def on_result(op, result):
                if result.status == "ok":
                    print(f"[{op.op_id}] ok — stripped {name!r} from {op.item_id}")
                else:
                    print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)

            rc = execute_plan_in_batches(
                graph=graph,
                logger=logger,
                ops=strip_ops,
                fetch_before=fetch_before,
                parse_before=parse_before,
                start_op=start_categorize,
                finish_op=finish_categorize,
                on_result=on_result,
            )
            if rc != 0:
                print(
                    "mail categories remove: per-message strip had errors; "
                    "skipping master-category delete.",
                    file=sys.stderr,
                )
                return rc
        else:
            print(f"mail categories remove: no messages tagged with {name!r}.")

    op = Operation(
        op_id=new_op_id(), action="mail.categories.remove",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.id,
        args={"auth_mode": auth_mode},
    )
    result = execute_remove_category(op, graph, logger, before=before)
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
