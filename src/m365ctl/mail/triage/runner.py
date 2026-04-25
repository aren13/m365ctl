"""Triage runner — orchestrate validate / emit / execute paths."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from m365ctl.common.audit import AuditLogger
from m365ctl.common.config import AuthMode, Config
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, Plan, write_plan
from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.triage.dsl import DslError, load_ruleset_from_yaml
from m365ctl.mail.triage.plan import build_plan


class RunnerError(RuntimeError):
    """Raised when validation/emit/execute fails."""


def run_validate(rules_path: Path | str) -> None:
    """Parse + shape-check the YAML; raise RunnerError on any issue."""
    try:
        load_ruleset_from_yaml(rules_path)
    except DslError as e:
        raise RunnerError(str(e)) from e
    except (FileNotFoundError, OSError) as e:
        raise RunnerError(f"cannot read {rules_path}: {e}") from e


def run_emit(
    *,
    rules_path: Path,
    catalog_path: Path,
    mailbox_upn: str,
    scope: str,
    plan_out: Path,
) -> Plan:
    """Load DSL, query the catalog, emit a Plan, write it to plan_out."""
    try:
        ruleset = load_ruleset_from_yaml(rules_path)
    except DslError as e:
        raise RunnerError(str(e)) from e

    rows = _candidate_rows(catalog_path=catalog_path, mailbox_upn=mailbox_upn)
    plan = build_plan(
        ruleset, rows,
        mailbox_upn=mailbox_upn,
        source_cmd=f"mail triage run --rules {rules_path}",
        scope=scope,
        now=datetime.now(timezone.utc),
    )
    write_plan(plan, plan_out)
    return plan


def _candidate_rows(*, catalog_path: Path, mailbox_upn: str) -> list[dict[str, Any]]:
    if not catalog_path.exists():
        raise RunnerError(
            f"catalog not built at {catalog_path}; run 'mail catalog refresh' first"
        )
    with open_catalog(catalog_path) as conn:
        cur = conn.execute(
            """
            SELECT message_id, subject, from_address, from_name,
                   to_addresses, cc_addresses, body_preview,
                   parent_folder_path, received_at, is_read,
                   flag_status, has_attachments, importance,
                   categories, inference_class
            FROM mail_messages
            WHERE mailbox_upn = ? AND is_deleted = false
            """,
            [mailbox_upn],
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------- Execution path ----------
#
# Note: existing mail.mutate.* executors take a positional ``before: dict``
# kwarg (used by audit + undo to record pre-state). The runner does not have
# a Graph fetch in scope here, so we pass ``before={}`` — matching the
# executors' documented "best-effort empty before is acceptable" contract.
# The audit log still records the mutation; undo degrades to best-effort
# for these operations, which is acceptable for triage's bulk-rule path.


def _exec_move(
    op: Operation, *, cfg: Config, mailbox_spec: str,
    auth_mode: AuthMode, graph: GraphClient, logger: AuditLogger,
) -> Any:
    from m365ctl.mail.mutate.move import execute_move
    return execute_move(op, graph, logger, before={})


def _exec_copy(
    op: Operation, *, cfg: Config, mailbox_spec: str,
    auth_mode: AuthMode, graph: GraphClient, logger: AuditLogger,
) -> Any:
    from m365ctl.mail.mutate.copy import execute_copy
    return execute_copy(op, graph, logger, before={})


def _exec_delete(
    op: Operation, *, cfg: Config, mailbox_spec: str,
    auth_mode: AuthMode, graph: GraphClient, logger: AuditLogger,
) -> Any:
    from m365ctl.mail.mutate.delete import execute_soft_delete
    return execute_soft_delete(op, graph, logger, before={})


def _exec_flag(
    op: Operation, *, cfg: Config, mailbox_spec: str,
    auth_mode: AuthMode, graph: GraphClient, logger: AuditLogger,
) -> Any:
    from m365ctl.mail.mutate.flag import execute_flag
    return execute_flag(op, graph, logger, before={})


def _exec_read(
    op: Operation, *, cfg: Config, mailbox_spec: str,
    auth_mode: AuthMode, graph: GraphClient, logger: AuditLogger,
) -> Any:
    from m365ctl.mail.mutate.read import execute_read
    return execute_read(op, graph, logger, before={})


def _exec_focus(
    op: Operation, *, cfg: Config, mailbox_spec: str,
    auth_mode: AuthMode, graph: GraphClient, logger: AuditLogger,
) -> Any:
    from m365ctl.mail.mutate.focus import execute_focus
    return execute_focus(op, graph, logger, before={})


def _exec_categorize(
    op: Operation, *, cfg: Config, mailbox_spec: str,
    auth_mode: AuthMode, graph: GraphClient, logger: AuditLogger,
) -> Any:
    from m365ctl.mail.mutate.categorize import execute_categorize
    return execute_categorize(op, graph, logger, before={})


_EXECUTORS: dict[str, Callable[..., Any]] = {
    "mail.move":         _exec_move,
    "mail.copy":         _exec_copy,
    "mail.delete.soft":  _exec_delete,
    "mail.flag":         _exec_flag,
    "mail.read":         _exec_read,
    "mail.focus":        _exec_focus,
    "mail.categorize":   _exec_categorize,
}


def run_execute(
    plan: Plan,
    *,
    cfg: Config,
    mailbox_spec: str,
    auth_mode: AuthMode,
    graph: GraphClient,
    logger: AuditLogger,
) -> list[Any]:
    """Dispatch each operation to its executor; collect per-op results.

    Continues past per-op failures so a single bad message doesn't abort
    the whole batch. Caller decides exit code from result statuses.
    """
    results: list[Any] = []
    for op in plan.operations:
        executor = _EXECUTORS.get(op.action)
        if executor is None:
            raise RunnerError(f"no executor for action {op.action!r}")
        try:
            r = executor(
                op, cfg=cfg, mailbox_spec=mailbox_spec,
                auth_mode=auth_mode, graph=graph, logger=logger,
            )
        except Exception as e:
            from types import SimpleNamespace
            r = SimpleNamespace(status="error", error=str(e))
        results.append(r)
    return results
