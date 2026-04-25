"""Server-side inbox-rule mutators with audit + undo support.

All five executors follow the existing mail-mutate convention:

    execute_<verb>(op, graph, logger, *, before: dict) -> RuleResult

Each writes one ``log_mutation_start`` + ``log_mutation_end`` pair to the
audit log, mirroring ``mail.mutate.move`` etc.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.endpoints import AuthMode, user_base


@dataclass
class RuleResult:
    op_id: str
    status: str  # "ok" | "error"
    error: str | None = None
    after: dict[str, Any] = field(default_factory=dict)


def _path_rules(mailbox_spec: str, auth_mode: str) -> str:
    mode: AuthMode = "app-only" if auth_mode == "app-only" else "delegated"
    ub = user_base(mailbox_spec, auth_mode=mode)
    return f"{ub}/mailFolders/inbox/messageRules"


def execute_create(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> RuleResult:
    args = op.args
    base = _path_rules(args["mailbox_spec"], args["auth_mode"])
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-rule-create",
        args=args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        created = graph.post(base, json=args["body"])
    except GraphError as e:
        log_mutation_end(
            logger, op_id=op.op_id, after=None, result="error", error=str(e),
        )
        return RuleResult(op_id=op.op_id, status="error", error=str(e), after={})
    after: dict[str, Any] = {"id": created.get("id", "")}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return RuleResult(op_id=op.op_id, status="ok", error=None, after=created)


def execute_update(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> RuleResult:
    args = op.args
    base = _path_rules(args["mailbox_spec"], args["auth_mode"])
    path = f"{base}/{args['rule_id']}"
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-rule-update",
        args=args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        updated = graph.patch(path, json_body=args["body"])
    except GraphError as e:
        log_mutation_end(
            logger, op_id=op.op_id, after=None, result="error", error=str(e),
        )
        return RuleResult(op_id=op.op_id, status="error", error=str(e), after={})
    after: dict[str, Any] = {"id": updated.get("id", "")}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return RuleResult(op_id=op.op_id, status="ok", error=None, after=updated)


def execute_delete(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> RuleResult:
    args = op.args
    base = _path_rules(args["mailbox_spec"], args["auth_mode"])
    path = f"{base}/{args['rule_id']}"
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-rule-delete",
        args=args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.delete(path)
    except GraphError as e:
        log_mutation_end(
            logger, op_id=op.op_id, after=None, result="error", error=str(e),
        )
        return RuleResult(op_id=op.op_id, status="error", error=str(e), after={})
    log_mutation_end(logger, op_id=op.op_id, after=None, result="ok")
    return RuleResult(op_id=op.op_id, status="ok", error=None, after={})


def execute_set_enabled(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> RuleResult:
    args = op.args
    base = _path_rules(args["mailbox_spec"], args["auth_mode"])
    path = f"{base}/{args['rule_id']}"
    body = {"isEnabled": bool(args["is_enabled"])}
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-rule-set-enabled",
        args=args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        updated = graph.patch(path, json_body=body)
    except GraphError as e:
        log_mutation_end(
            logger, op_id=op.op_id, after=None, result="error", error=str(e),
        )
        return RuleResult(op_id=op.op_id, status="error", error=str(e), after={})
    after: dict[str, Any] = {"id": updated.get("id", ""),
                              "isEnabled": updated.get("isEnabled")}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return RuleResult(op_id=op.op_id, status="ok", error=None, after=updated)


def execute_reorder(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> RuleResult:
    """Reorder via per-rule PATCH of the ``sequence`` field.

    ``args.ordering`` is a list of ``{rule_id, sequence}`` dicts in the
    desired evaluation order. Each is written as given — caller is
    responsible for picking a sane spread (e.g. 10, 20, 30 ...).

    For undo, we need the *prior* sequence-by-rule mapping. If the caller
    didn't pre-record it in ``before["ordering"]``, we GET the current
    rules and capture them ourselves before issuing PATCHes.
    """
    args = op.args
    base = _path_rules(args["mailbox_spec"], args["auth_mode"])

    # Backfill `before.ordering` if the caller didn't supply it; needed by
    # the inverse-builder so undo can restore the prior sequence numbers.
    if not before.get("ordering"):
        try:
            current = graph.get(base)
            prior = [
                {"rule_id": r.get("id", ""), "sequence": r.get("sequence", 0)}
                for r in current.get("value", [])
                if r.get("id")
            ]
            before = {**before, "ordering": prior}
        except GraphError:
            # Couldn't fetch — record empty ordering; undo will fail loudly.
            before = {**before, "ordering": []}

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-rule-reorder",
        args=args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    errors: list[str] = []
    for entry in args["ordering"]:
        path = f"{base}/{entry['rule_id']}"
        try:
            graph.patch(path, json_body={"sequence": int(entry["sequence"])})
        except GraphError as e:
            errors.append(f"{entry['rule_id']}: {e}")
    if errors:
        msg = "; ".join(errors)
        log_mutation_end(
            logger, op_id=op.op_id, after=None, result="error", error=msg,
        )
        return RuleResult(op_id=op.op_id, status="error", error=msg, after={})
    after: dict[str, Any] = {"ordering": args["ordering"]}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return RuleResult(
        op_id=op.op_id, status="ok", error=None,
        after={"ordering": args["ordering"]},
    )
