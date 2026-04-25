"""Triage plan emitter — RuleSet × catalog rows → Plan."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from m365ctl.common.planfile import (
    PLAN_SCHEMA_VERSION, Operation, Plan, new_op_id,
)
from m365ctl.mail.triage.dsl import (
    Action, CategorizeA, CopyA, DeleteA, FlagA, FocusSetA, MoveA,
    ReadA, Rule, RuleSet,
)
from m365ctl.mail.triage.match import MatchContext, evaluate_match


def build_plan(
    ruleset: RuleSet,
    rows: Iterable[dict[str, Any]],
    *,
    mailbox_upn: str,
    source_cmd: str,
    scope: str,
    now: datetime,
) -> Plan:
    ops: list[Operation] = []
    rows_list = list(rows)
    context = _build_match_context(rows_list)
    for rule in ruleset.rules:
        if not rule.enabled:
            continue
        for row in rows_list:
            if not evaluate_match(rule.match, row, now=now, context=context):
                continue
            for action in rule.actions:
                ops.append(_op_for(rule, action, row, mailbox_upn=mailbox_upn))
    return Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at=now.isoformat(),
        source_cmd=source_cmd,
        scope=scope,
        operations=ops,
    )


def _build_match_context(rows: list[dict[str, Any]]) -> MatchContext:
    """A conversation is 'replied' iff it has >=2 distinct senders."""
    senders_by_conv: dict[str, set[str]] = {}
    for r in rows:
        cid = r.get("conversation_id")
        sender = (r.get("from_address") or "").lower()
        if cid and sender:
            senders_by_conv.setdefault(cid, set()).add(sender)
    return MatchContext(
        replied_conversations=frozenset(
            cid for cid, senders in senders_by_conv.items() if len(senders) > 1
        ),
    )


def _op_for(rule: Rule, action: Action, row: dict, *, mailbox_upn: str) -> Operation:
    args: dict[str, Any] = {"rule_name": rule.name}
    if isinstance(action, MoveA):
        return _op("mail.move", row, mailbox_upn,
                   {**args, "to_folder": action.to_folder})
    if isinstance(action, CopyA):
        return _op("mail.copy", row, mailbox_upn,
                   {**args, "to_folder": action.to_folder})
    if isinstance(action, DeleteA):
        return _op("mail.delete.soft", row, mailbox_upn, args)
    if isinstance(action, FlagA):
        merged = {**args, "status": action.status}
        if action.due_days is not None:
            merged["due_days"] = action.due_days
        return _op("mail.flag", row, mailbox_upn, merged)
    if isinstance(action, ReadA):
        return _op("mail.read", row, mailbox_upn, {**args, "is_read": action.value})
    if isinstance(action, FocusSetA):
        return _op("mail.focus", row, mailbox_upn, {**args, "focus": action.value})
    if isinstance(action, CategorizeA):
        merged = dict(args)
        if action.add is not None:
            merged["add"] = list(action.add)
        if action.remove is not None:
            merged["remove"] = list(action.remove)
        if action.set_ is not None:
            merged["set"] = list(action.set_)
        return _op("mail.categorize", row, mailbox_upn, merged)
    raise TypeError(f"unhandled action: {type(action).__name__}")  # pragma: no cover


def _op(action: str, row: dict, mailbox_upn: str, args: dict) -> Operation:
    return Operation(
        op_id=new_op_id(),
        action=action,
        drive_id=mailbox_upn,
        item_id=row["message_id"],
        args=args,
        dry_run_result=_dry_run_summary(action, row, args),
    )


def _dry_run_summary(action: str, row: dict, args: dict) -> str:
    subj = (row.get("subject") or "")[:60]
    sender = row.get("from_address") or "?"
    rule = args.get("rule_name", "?")
    summary = {
        "mail.move":       f"would move → {args.get('to_folder')}",
        "mail.copy":       f"would copy → {args.get('to_folder')}",
        "mail.delete.soft": "would soft-delete → Deleted Items",
        "mail.flag":       f"would flag (status={args.get('status')})",
        "mail.read":       f"would mark read={args.get('is_read')}",
        "mail.focus":      f"would set focus={args.get('focus')}",
        "mail.categorize": f"would categorize (add={args.get('add')}, "
                           f"remove={args.get('remove')}, set={args.get('set')})",
    }.get(action, f"would {action}")
    return f"[{rule}] {summary}: {sender} | {subj}"
