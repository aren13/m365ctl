from __future__ import annotations

from datetime import datetime, timezone

from m365ctl.mail.triage.dsl import (
    CategorizeA, FlagA, FromP, Match, MoveA, ReadA, Rule, RuleSet,
    UnreadP,
)
from m365ctl.mail.triage.plan import build_plan


_NOW = datetime(2026, 4, 25, tzinfo=timezone.utc)


def _ruleset(rules: list[Rule]) -> RuleSet:
    return RuleSet(version=1, mailbox="me", rules=rules)


def _row(message_id: str, **overrides):
    base = {
        "message_id": message_id,
        "subject": "x",
        "from_address": "alice@example.com",
        "parent_folder_path": "Inbox",
        "received_at": _NOW,
        "is_read": False,
        "flag_status": "notFlagged",
        "has_attachments": False,
        "importance": "normal",
        "categories": "",
        "inference_class": "focused",
    }
    base.update(overrides)
    return base


def test_no_matching_messages_yields_empty_plan():
    rs = _ruleset([
        Rule(name="r1", enabled=True,
             match=Match(all_of=[UnreadP(value=True)]),
             actions=[ReadA(value=True)]),
    ])
    plan = build_plan(rs, [_row("m1", is_read=True)],
                     mailbox_upn="me", source_cmd="x", scope="me", now=_NOW)
    assert plan.operations == []


def test_one_rule_one_action_one_message_per_op():
    rs = _ruleset([
        Rule(name="archive", enabled=True,
             match=Match(all_of=[FromP(domain_in=["example.com"])]),
             actions=[MoveA(to_folder="Archive")]),
    ])
    plan = build_plan(
        rs, [_row("m1"), _row("m2", from_address="bob@example.com"),
             _row("m3", from_address="x@other.com")],
        mailbox_upn="me", source_cmd="mail triage run --rules x.yaml",
        scope="me", now=_NOW,
    )
    assert len(plan.operations) == 2
    assert {op.item_id for op in plan.operations} == {"m1", "m2"}
    for op in plan.operations:
        assert op.action == "mail.move"
        assert op.args["to_folder"] == "Archive"
        assert op.args["rule_name"] == "archive"
        assert op.drive_id == "me"


def test_multiple_actions_emit_one_op_per_action_per_match():
    rs = _ruleset([
        Rule(name="combo", enabled=True,
             match=Match(all_of=[UnreadP(value=True)]),
             actions=[
                 CategorizeA(add=["X"]),
                 FlagA(status="flagged", due_days=2),
             ]),
    ])
    plan = build_plan(rs, [_row("m1"), _row("m2", is_read=True)],
                     mailbox_upn="me", source_cmd="x", scope="me", now=_NOW)
    actions = [op.action for op in plan.operations]
    assert actions == ["mail.categorize", "mail.flag"]
    assert plan.operations[1].args == {
        "rule_name": "combo", "status": "flagged", "due_days": 2,
    }


def test_disabled_rules_are_skipped():
    rs = _ruleset([
        Rule(name="off", enabled=False,
             match=Match(), actions=[ReadA(value=True)]),
        Rule(name="on", enabled=True,
             match=Match(), actions=[ReadA(value=True)]),
    ])
    plan = build_plan(rs, [_row("m1")], mailbox_upn="me", source_cmd="x",
                     scope="me", now=_NOW)
    rule_names = {op.args["rule_name"] for op in plan.operations}
    assert rule_names == {"on"}


def test_rules_stack_in_declaration_order():
    rs = _ruleset([
        Rule(name="first", enabled=True, match=Match(),
             actions=[ReadA(value=True)]),
        Rule(name="second", enabled=True, match=Match(),
             actions=[FlagA(status="flagged")]),
    ])
    plan = build_plan(rs, [_row("m1")], mailbox_upn="me", source_cmd="x",
                     scope="me", now=_NOW)
    assert [op.args["rule_name"] for op in plan.operations] == ["first", "second"]


def test_categorize_carries_add_remove_set():
    rs = _ruleset([
        Rule(name="cat", enabled=True, match=Match(),
             actions=[CategorizeA(add=["A"], remove=["B"], set_=["C", "D"])]),
    ])
    plan = build_plan(rs, [_row("m1")], mailbox_upn="me", source_cmd="x",
                     scope="me", now=_NOW)
    args = plan.operations[0].args
    assert args["add"] == ["A"]
    assert args["remove"] == ["B"]
    assert args["set"] == ["C", "D"]


def test_plan_metadata():
    rs = _ruleset([
        Rule(name="r", enabled=True, match=Match(),
             actions=[ReadA(value=True)]),
    ])
    plan = build_plan(rs, [_row("m1")], mailbox_upn="me",
                     source_cmd="mail triage run --rules x.yaml",
                     scope="me", now=_NOW)
    assert plan.version == 1
    assert plan.scope == "me"
    assert plan.source_cmd == "mail triage run --rules x.yaml"
