"""`m365ctl mail rules list|show` — read-only inbox rules (CRUD lands Phase 8)."""
from __future__ import annotations

import argparse

from m365ctl.common.graph import GraphClient
from m365ctl.mail.cli._common import add_common_args, emit_json_lines, load_and_authorize
from m365ctl.mail.rules import get_rule, list_rules


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail rules")
    add_common_args(p)
    sub = p.add_subparsers(dest="subcommand", required=True)
    lst = sub.add_parser("list", help="List rules by evaluation order.")
    lst.add_argument("--disabled", action="store_true",
                     help="Show disabled rules too (default: enabled only).")
    show = sub.add_parser("show", help="Show a single rule.")
    show.add_argument("rule_id")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    if args.subcommand == "list":
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

    if args.subcommand == "show":
        rule = get_rule(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode, rule_id=args.rule_id)
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
    return 2
