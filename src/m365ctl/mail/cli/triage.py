"""`m365ctl mail triage {validate, run}` — DSL → plan → confirm."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.config import load_config
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import load_plan
from m365ctl.common.safety import assert_mailbox_allowed
from m365ctl.mail.cli._common import derive_mailbox_upn, load_and_authorize
from m365ctl.mail.triage.runner import (
    RunnerError, run_emit, run_execute, run_validate,
)


def _validate_main(args: argparse.Namespace) -> int:
    try:
        run_validate(args.rules)
    except RunnerError as e:
        print(f"invalid: {e}", file=sys.stderr)
        return 2
    print(f"ok: {args.rules} parses + validates cleanly.")
    return 0


def _run_main(args: argparse.Namespace) -> int:
    if args.rules and args.from_plan:
        print("error: --rules and --from-plan are mutually exclusive",
              file=sys.stderr)
        return 2
    if not args.rules and not args.from_plan:
        print("error: provide either --rules <yaml> or --from-plan <json>",
              file=sys.stderr)
        return 2

    cfg = load_config(Path(args.config))
    mailbox_spec = args.mailbox
    auth_mode = cfg.default_auth if mailbox_spec == "me" else "app-only"
    assert_mailbox_allowed(
        mailbox_spec, cfg, auth_mode=auth_mode, unsafe_scope=args.unsafe_scope,
    )
    mailbox_upn = derive_mailbox_upn(mailbox_spec)

    if args.rules:
        # Plan path: emit (and optionally execute when --confirm).
        plan_out = Path(args.plan_out) if args.plan_out else None
        if plan_out is None and not args.confirm:
            print(
                "error: provide --plan-out (dry run) or --confirm (execute)",
                file=sys.stderr,
            )
            return 2
        try:
            if plan_out is None:
                # Implicit: stage to a temp file, execute, then discard.
                import tempfile
                plan_out = Path(tempfile.mkstemp(suffix=".plan.json")[1])
                emit_only = False
            else:
                emit_only = True
            plan = run_emit(
                rules_path=Path(args.rules),
                catalog_path=cfg.mail.catalog_path,
                mailbox_upn=mailbox_upn,
                scope=mailbox_spec,
                plan_out=plan_out,
            )
        except RunnerError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"plan: {len(plan.operations)} operations -> {plan_out}")
        if emit_only:
            return 0
    else:
        # --from-plan path: load + execute (only with --confirm).
        if not args.confirm:
            print("error: --from-plan requires --confirm", file=sys.stderr)
            return 2
        plan = load_plan(Path(args.from_plan))

    # Execute path.
    if not args.confirm:
        return 0  # already returned above for emit-only, but keep belt+braces
    _cfg, _auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    results = run_execute(
        plan,
        cfg=cfg,
        mailbox_spec=mailbox_spec,
        auth_mode=auth_mode,
        graph=graph,
        logger=logger,
    )
    ok = sum(1 for r in results if getattr(r, "status", "") == "ok")
    bad = len(results) - ok
    print(f"executed: {ok} ok, {bad} error(s)")
    return 1 if bad else 0


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", default="config.toml")
    p.add_argument("--mailbox", default="me")
    p.add_argument("--unsafe-scope", action="store_true")
    p.add_argument("--confirm", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail triage")
    _add_common(p)
    sub = p.add_subparsers(dest="subcommand", required=True)

    v = sub.add_parser("validate", help="Parse + shape-check rules YAML.")
    _add_common(v)
    v.add_argument("rules", help="Path to rules YAML.")

    r = sub.add_parser("run", help="Emit a plan from rules, or execute a plan.")
    _add_common(r)
    r.add_argument("--rules", help="Path to rules YAML (emit mode).")
    r.add_argument("--from-plan", help="Path to plan.json (execute mode).")
    r.add_argument("--plan-out", help="Write plan to this path and exit.")

    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.subcommand == "validate":
        return _validate_main(args)
    if args.subcommand == "run":
        return _run_main(args)
    return 2
