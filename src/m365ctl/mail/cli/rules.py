"""`m365ctl mail rules` — Phase 1 list/show + Phase 8 CRUD verbs.

Subcommands:
    list / show                       — read-only (Phase 1)
    create  --from-file rule.yaml     — POST one rule
    update  <id> --from-file rule.yaml — PATCH one rule
    delete  <id>                       — DELETE one rule
    enable / disable <id>              — flip ``isEnabled``
    reorder --from-file ordering.yaml  — bulk PATCH of ``sequence``
    export  [--out PATH]               — emit YAML doc with ``rules: [...]``
    import  --from-file rules.yaml     — apply YAML doc; ``--replace`` first
                                          deletes existing rules

Folder-path ↔ id translation reuses Phase 2's ``resolve_folder_path``
(forward) and a one-shot ``list_folders`` walk to build the reverse map.
The CLI never logs audit lines itself — each ``execute_<verb>`` writes
its own ``log_mutation_start`` + ``log_mutation_end`` pair.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable

import yaml  # type: ignore[import-untyped]

from m365ctl.common.audit import AuditLogger
from m365ctl.common.auth import AppOnlyCredential, DelegatedCredential
from m365ctl.common.config import AuthMode, Config
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.cli._common import (
    add_common_args,
    emit_json_lines,
    load_and_authorize,
)
from m365ctl.mail.folders import FolderNotFound, list_folders, resolve_folder_path
from m365ctl.mail.mutate._common import derive_mailbox_upn
from m365ctl.mail.mutate.rules import (
    execute_create,
    execute_delete,
    execute_reorder,
    execute_set_enabled,
    execute_update,
)
from m365ctl.mail.rules import get_rule, list_rules, rule_from_yaml, rule_to_yaml


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail rules")
    add_common_args(p)
    sub = p.add_subparsers(dest="subcommand", required=True)

    lst = sub.add_parser("list", help="List rules by evaluation order.")
    lst.add_argument("--disabled", action="store_true",
                     help="Show disabled rules too (default: enabled only).")

    show = sub.add_parser("show", help="Show a single rule.")
    show.add_argument("rule_id")

    c = sub.add_parser("create", help="Create a rule from a YAML file.")
    c.add_argument("--from-file", dest="from_file", required=True,
                   help="Path to rule YAML.")
    c.add_argument("--confirm", action="store_true",
                   help="Required to execute (otherwise dry-run).")

    u = sub.add_parser("update", help="Update a rule from a YAML file.")
    u.add_argument("rule_id")
    u.add_argument("--from-file", dest="from_file", required=True,
                   help="Path to rule YAML.")
    u.add_argument("--confirm", action="store_true")

    d = sub.add_parser("delete", help="Delete a rule.")
    d.add_argument("rule_id")
    d.add_argument("--confirm", action="store_true")

    en = sub.add_parser("enable", help="Enable a rule (set isEnabled=true).")
    en.add_argument("rule_id")
    en.add_argument("--confirm", action="store_true")

    di = sub.add_parser("disable", help="Disable a rule (set isEnabled=false).")
    di.add_argument("rule_id")
    di.add_argument("--confirm", action="store_true")

    ro = sub.add_parser("reorder", help="Bulk-reorder rules from a YAML file.")
    ro.add_argument("--from-file", dest="from_file", required=True,
                    help="Path to ordering YAML: [{rule_id, sequence}, ...].")
    ro.add_argument("--confirm", action="store_true")

    ex = sub.add_parser("export", help="Export all rules to YAML.")
    ex.add_argument("--out", dest="out", default=None,
                    help="Output path (default: stdout).")

    im = sub.add_parser("import", help="Import rules from YAML.")
    im.add_argument("--from-file", dest="from_file", required=True,
                    help="Path to YAML doc with a top-level 'rules:' list.")
    im.add_argument("--replace", action="store_true",
                    help="Delete all existing rules first, then create.")
    im.add_argument("--confirm", action="store_true")

    return p


# ---- helpers ---------------------------------------------------------------


def _build_logger(cfg: Config) -> AuditLogger:
    return AuditLogger(ops_dir=cfg.logging.ops_dir)


def _build_folder_maps(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
) -> tuple[Callable[[str], str], Callable[[str], str]]:
    """One-shot walk of ``list_folders`` to build bidirectional path↔id maps.

    Returns ``(path_to_id, id_to_path)``. ``path_to_id`` falls back to
    ``resolve_folder_path`` (which handles well-known names like ``inbox``)
    on a miss. ``id_to_path`` returns the raw id verbatim if unknown so a
    round-trip never silently drops a moveToFolder reference for a folder
    the user no longer has access to.
    """
    id_to_path_map: dict[str, str] = {}
    path_to_id_map: dict[str, str] = {}
    for f in list_folders(graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode):
        id_to_path_map[f.id] = f.path
        path_to_id_map[f.path.lower()] = f.id

    def _path_to_id(path: str) -> str:
        key = path.strip("/").lower()
        if key in path_to_id_map:
            return path_to_id_map[key]
        # Fall back to resolve_folder_path for well-known names
        # (``inbox``/``drafts``/...) which the flat walk doesn't surface.
        return resolve_folder_path(
            path, graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
        )

    def _id_to_path(folder_id: str) -> str:
        return id_to_path_map.get(folder_id, folder_id)

    return _path_to_id, _id_to_path


def _load_yaml(path: str) -> Any:
    return yaml.safe_load(Path(path).read_text())


# ---- read handlers (unchanged from Phase 1) -------------------------------


def _run_list(args: argparse.Namespace) -> int:
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    rules = list_rules(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)
    if not args.disabled:
        rules = [r for r in rules if r.is_enabled]
    if args.json:
        emit_json_lines(rules)
    else:
        for r in rules:
            enabled = "y" if r.is_enabled else "n"
            print(f"{r.sequence:<4} {enabled}  {r.display_name}  (id: {r.id})")
    return 0


def _run_show(args: argparse.Namespace) -> int:
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    rule = get_rule(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                    rule_id=args.rule_id)
    if args.json:
        emit_json_lines([rule])
    else:
        print(f"id:          {rule.id}")
        print(f"name:        {rule.display_name}")
        print(f"sequence:    {rule.sequence}")
        print(f"enabled:     {rule.is_enabled}")
        print(f"has_error:   {rule.has_error}")
        print(f"read_only:   {rule.is_read_only}")
        print(f"conditions:  {rule.conditions}")
        print(f"actions:     {rule.actions}")
        print(f"exceptions:  {rule.exceptions}")
    return 0


# ---- mutation handlers -----------------------------------------------------


def _authorize(args: argparse.Namespace) -> tuple[
    Config, AuthMode, DelegatedCredential | AppOnlyCredential, GraphClient,
]:
    cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    return cfg, auth_mode, cred, graph


def _run_create(args: argparse.Namespace) -> int:
    doc = _load_yaml(args.from_file)
    if not args.confirm:
        print(
            f"(dry-run) would create rule {doc.get('display_name', '?')!r} "
            f"from {args.from_file}",
            file=sys.stderr,
        )
        return 0
    cfg, auth_mode, _cred, graph = _authorize(args)
    path_to_id, _ = _build_folder_maps(
        graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
    )
    body = rule_from_yaml(doc, folder_path_to_id=path_to_id)
    op = Operation(
        op_id=new_op_id(),
        action="mail.rule.create",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id="",
        args={"mailbox_spec": args.mailbox, "auth_mode": auth_mode, "body": body},
    )
    result = execute_create(op, graph, _build_logger(cfg), before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    new_id = (result.after or {}).get("id", "")
    print(f"[{op.op_id}] ok — created rule {body.get('displayName')!r} (id: {new_id})")
    return 0


def _run_update(args: argparse.Namespace) -> int:
    doc = _load_yaml(args.from_file)
    if not args.confirm:
        print(
            f"(dry-run) would update rule {args.rule_id!r} from {args.from_file}",
            file=sys.stderr,
        )
        return 0
    cfg, auth_mode, _cred, graph = _authorize(args)
    path_to_id, _ = _build_folder_maps(
        graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
    )
    body = rule_from_yaml(doc, folder_path_to_id=path_to_id)
    op = Operation(
        op_id=new_op_id(),
        action="mail.rule.update",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id=args.rule_id,
        args={
            "mailbox_spec": args.mailbox, "auth_mode": auth_mode,
            "rule_id": args.rule_id, "body": body,
        },
    )
    result = execute_update(op, graph, _build_logger(cfg), before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — updated rule {args.rule_id!r}")
    return 0


def _run_delete(args: argparse.Namespace) -> int:
    if not args.confirm:
        print(
            f"mail rules delete: --confirm required to delete {args.rule_id!r}",
            file=sys.stderr,
        )
        return 2
    cfg, auth_mode, _cred, graph = _authorize(args)
    op = Operation(
        op_id=new_op_id(),
        action="mail.rule.delete",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id=args.rule_id,
        args={
            "mailbox_spec": args.mailbox, "auth_mode": auth_mode,
            "rule_id": args.rule_id,
        },
    )
    result = execute_delete(op, graph, _build_logger(cfg), before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — deleted rule {args.rule_id!r}")
    return 0


def _run_set_enabled(args: argparse.Namespace, *, is_enabled: bool) -> int:
    if not args.confirm:
        verb = "enable" if is_enabled else "disable"
        print(
            f"mail rules {verb}: --confirm required for {args.rule_id!r}",
            file=sys.stderr,
        )
        return 2
    cfg, auth_mode, _cred, graph = _authorize(args)
    op = Operation(
        op_id=new_op_id(),
        action="mail.rule.set-enabled",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id=args.rule_id,
        args={
            "mailbox_spec": args.mailbox, "auth_mode": auth_mode,
            "rule_id": args.rule_id, "is_enabled": is_enabled,
        },
    )
    result = execute_set_enabled(op, graph, _build_logger(cfg), before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    state = "enabled" if is_enabled else "disabled"
    print(f"[{op.op_id}] ok — {state} rule {args.rule_id!r}")
    return 0


def _run_reorder(args: argparse.Namespace) -> int:
    doc = _load_yaml(args.from_file)
    if not isinstance(doc, list):
        print(
            "mail rules reorder: --from-file must be a YAML list of "
            "{rule_id, sequence} entries",
            file=sys.stderr,
        )
        return 2
    if not args.confirm:
        print(
            f"(dry-run) would reorder {len(doc)} rule(s) from {args.from_file}",
            file=sys.stderr,
        )
        return 0
    ordering = [
        {"rule_id": str(e["rule_id"]), "sequence": int(e["sequence"])}
        for e in doc
    ]
    cfg, auth_mode, _cred, graph = _authorize(args)
    op = Operation(
        op_id=new_op_id(),
        action="mail.rule.reorder",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id="",
        args={
            "mailbox_spec": args.mailbox, "auth_mode": auth_mode,
            "ordering": ordering,
        },
    )
    result = execute_reorder(op, graph, _build_logger(cfg), before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — reordered {len(ordering)} rule(s)")
    return 0


def _run_export(args: argparse.Namespace) -> int:
    _cfg, auth_mode, _cred, graph = _authorize(args)
    _, id_to_path = _build_folder_maps(
        graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
    )
    rules = list_rules(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)
    docs = [rule_to_yaml(r, folder_id_to_path=id_to_path) for r in rules]
    out_doc = {"rules": docs}
    text = yaml.safe_dump(out_doc, sort_keys=False, default_flow_style=False)
    if args.out:
        Path(args.out).write_text(text)
        print(f"exported {len(docs)} rule(s) to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def _run_import(args: argparse.Namespace) -> int:
    doc = _load_yaml(args.from_file)
    if not isinstance(doc, dict) or "rules" not in doc:
        print(
            "mail rules import: --from-file must be a YAML mapping with a "
            "top-level 'rules:' list",
            file=sys.stderr,
        )
        return 2
    rules_yaml = doc["rules"]
    if not isinstance(rules_yaml, list):
        print(
            "mail rules import: 'rules' must be a list",
            file=sys.stderr,
        )
        return 2
    if not args.confirm:
        verb = "replace" if args.replace else "create"
        print(
            f"(dry-run) would {verb} {len(rules_yaml)} rule(s) "
            f"from {args.from_file}",
            file=sys.stderr,
        )
        return 0

    cfg, auth_mode, _cred, graph = _authorize(args)
    logger = _build_logger(cfg)
    path_to_id, _ = _build_folder_maps(
        graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
    )

    errors: list[str] = []

    if args.replace:
        existing = list_rules(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)
        for r in existing:
            op = Operation(
                op_id=new_op_id(),
                action="mail.rule.delete",
                drive_id=derive_mailbox_upn(args.mailbox),
                item_id=r.id,
                args={
                    "mailbox_spec": args.mailbox, "auth_mode": auth_mode,
                    "rule_id": r.id,
                },
            )
            result = execute_delete(op, graph, logger, before={})
            if result.status != "ok":
                errors.append(f"delete {r.id}: {result.error}")
                print(f"error deleting {r.id}: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] deleted {r.display_name!r} (id: {r.id})")

    for entry in rules_yaml:
        try:
            body = rule_from_yaml(entry, folder_path_to_id=path_to_id)
        except (FolderNotFound, ValueError) as e:
            name = entry.get("display_name", "?") if isinstance(entry, dict) else "?"
            errors.append(f"parse {name}: {e}")
            print(f"error parsing {name!r}: {e}", file=sys.stderr)
            continue
        op = Operation(
            op_id=new_op_id(),
            action="mail.rule.create",
            drive_id=derive_mailbox_upn(args.mailbox),
            item_id="",
            args={
                "mailbox_spec": args.mailbox, "auth_mode": auth_mode,
                "body": body,
            },
        )
        result = execute_create(op, graph, logger, before={})
        if result.status != "ok":
            errors.append(f"create {body.get('displayName', '?')}: {result.error}")
            print(
                f"error creating {body.get('displayName')!r}: {result.error}",
                file=sys.stderr,
            )
        else:
            new_id = (result.after or {}).get("id", "")
            print(f"[{op.op_id}] created {body.get('displayName')!r} (id: {new_id})")

    if errors:
        print(f"completed with {len(errors)} error(s)", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.subcommand == "list":
        return _run_list(args)
    if args.subcommand == "show":
        return _run_show(args)
    if args.subcommand == "create":
        return _run_create(args)
    if args.subcommand == "update":
        return _run_update(args)
    if args.subcommand == "delete":
        return _run_delete(args)
    if args.subcommand == "enable":
        return _run_set_enabled(args, is_enabled=True)
    if args.subcommand == "disable":
        return _run_set_enabled(args, is_enabled=False)
    if args.subcommand == "reorder":
        return _run_reorder(args)
    if args.subcommand == "export":
        return _run_export(args)
    if args.subcommand == "import":
        return _run_import(args)
    return 2
