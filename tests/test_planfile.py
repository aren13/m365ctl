from __future__ import annotations

import json
from pathlib import Path

import pytest

from m365ctl.common.planfile import (
    PLAN_SCHEMA_VERSION,
    Operation,
    Plan,
    PlanFileError,
    load_plan,
    write_plan,
)


def _op(**over) -> Operation:
    base = dict(
        op_id="00000000-0000-4000-8000-000000000001",
        action="move",
        drive_id="d1",
        item_id="i1",
        args={"new_parent_item_id": "parent-id"},
        dry_run_result="would move /A/foo.txt -> /B/foo.txt",
    )
    base.update(over)
    return Operation(**base)


def test_write_and_load_round_trip(tmp_path: Path) -> None:
    plan = Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at="2026-04-24T10:00:00+00:00",
        source_cmd="od-move --pattern '**/*.tmp' --scope me",
        scope="me",
        operations=[_op(), _op(op_id="00000000-0000-4000-8000-000000000002",
                         item_id="i2")],
    )
    p = tmp_path / "plan.json"
    write_plan(plan, p)

    loaded = load_plan(p)
    assert loaded.version == PLAN_SCHEMA_VERSION
    assert loaded.scope == "me"
    assert [o.item_id for o in loaded.operations] == ["i1", "i2"]
    assert loaded.operations[0].args == {"new_parent_item_id": "parent-id"}


def test_write_plan_emits_stable_json(tmp_path: Path) -> None:
    plan = Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at="2026-04-24T10:00:00+00:00",
        source_cmd="od-rename i1 new.txt",
        scope="drive:d1",
        operations=[_op(action="rename", args={"new_name": "new.txt"})],
    )
    p = tmp_path / "plan.json"
    write_plan(plan, p)

    raw = json.loads(p.read_text())
    assert raw["version"] == PLAN_SCHEMA_VERSION
    assert raw["operations"][0]["action"] == "rename"
    assert raw["operations"][0]["args"] == {"new_name": "new.txt"}


def test_load_plan_rejects_unknown_version(tmp_path: Path) -> None:
    p = tmp_path / "old.json"
    p.write_text(json.dumps({
        "version": 999,
        "created_at": "2026-04-24T10:00:00+00:00",
        "source_cmd": "x",
        "scope": "me",
        "operations": [],
    }))
    with pytest.raises(PlanFileError, match="unsupported plan version"):
        load_plan(p)


def test_load_plan_rejects_unknown_action(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({
        "version": PLAN_SCHEMA_VERSION,
        "created_at": "2026-04-24T10:00:00+00:00",
        "source_cmd": "x",
        "scope": "me",
        "operations": [{
            "op_id": "00000000-0000-4000-8000-000000000001",
            "action": "nuke",
            "drive_id": "d",
            "item_id": "i",
            "args": {},
            "dry_run_result": "",
        }],
    }))
    with pytest.raises(PlanFileError, match="unknown action 'nuke'"):
        load_plan(p)


def test_load_plan_rejects_missing_op_fields(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({
        "version": PLAN_SCHEMA_VERSION,
        "created_at": "2026-04-24T10:00:00+00:00",
        "source_cmd": "x",
        "scope": "me",
        "operations": [{"action": "move"}],
    }))
    with pytest.raises(PlanFileError, match="missing required op field"):
        load_plan(p)


def test_new_op_id_generates_uuid4() -> None:
    from m365ctl.common.planfile import new_op_id
    a, b = new_op_id(), new_op_id()
    assert a != b
    assert len(a) == 36 and a.count("-") == 4


def test_plan_loader_accepts_mail_folder_actions(tmp_path):
    from m365ctl.common.planfile import PLAN_SCHEMA_VERSION, load_plan
    import json
    path = tmp_path / "p.json"
    path.write_text(json.dumps({
        "version": PLAN_SCHEMA_VERSION,
        "created_at": "2026-04-24T00:00:00Z",
        "source_cmd": "mail-folders move",
        "scope": "me",
        "operations": [
            {"op_id": "1", "action": "mail.folder.create", "drive_id": "me", "item_id": "inbox", "args": {"name": "Triage"}},
            {"op_id": "2", "action": "mail.folder.rename", "drive_id": "me", "item_id": "f1", "args": {"new_name": "Triaged"}},
            {"op_id": "3", "action": "mail.folder.move", "drive_id": "me", "item_id": "f1", "args": {"destination_id": "archive"}},
            {"op_id": "4", "action": "mail.folder.delete", "drive_id": "me", "item_id": "f1", "args": {}},
        ],
    }))
    plan = load_plan(path)
    assert len(plan.operations) == 4
    assert [op.action for op in plan.operations] == [
        "mail.folder.create", "mail.folder.rename",
        "mail.folder.move", "mail.folder.delete",
    ]


def test_plan_loader_accepts_mail_categories_actions(tmp_path):
    from m365ctl.common.planfile import PLAN_SCHEMA_VERSION, load_plan
    import json
    path = tmp_path / "p.json"
    path.write_text(json.dumps({
        "version": PLAN_SCHEMA_VERSION,
        "created_at": "2026-04-24T00:00:00Z",
        "source_cmd": "mail-categories sync",
        "scope": "me",
        "operations": [
            {"op_id": "1", "action": "mail.categories.add", "drive_id": "me", "item_id": "", "args": {"name": "Waiting", "color": "preset0"}},
            {"op_id": "2", "action": "mail.categories.update", "drive_id": "me", "item_id": "c1", "args": {"name": "Waiting-New"}},
            {"op_id": "3", "action": "mail.categories.remove", "drive_id": "me", "item_id": "c1", "args": {}},
        ],
    }))
    plan = load_plan(path)
    assert len(plan.operations) == 3


def test_plan_loader_accepts_phase3_mail_actions(tmp_path):
    from m365ctl.common.planfile import PLAN_SCHEMA_VERSION, load_plan
    import json
    path = tmp_path / "p.json"
    path.write_text(json.dumps({
        "version": PLAN_SCHEMA_VERSION,
        "created_at": "2026-04-25T00:00:00Z",
        "source_cmd": "mail move --pattern",
        "scope": "me",
        "operations": [
            {"op_id": "1", "action": "mail.move",        "drive_id": "me", "item_id": "msg-1", "args": {"destination_id": "archive"}},
            {"op_id": "2", "action": "mail.copy",        "drive_id": "me", "item_id": "msg-2", "args": {"destination_id": "archive"}},
            {"op_id": "3", "action": "mail.flag",        "drive_id": "me", "item_id": "msg-3", "args": {"status": "flagged"}},
            {"op_id": "4", "action": "mail.read",        "drive_id": "me", "item_id": "msg-4", "args": {"is_read": True}},
            {"op_id": "5", "action": "mail.focus",       "drive_id": "me", "item_id": "msg-5", "args": {"inference_classification": "focused"}},
            {"op_id": "6", "action": "mail.categorize",  "drive_id": "me", "item_id": "msg-6", "args": {"set": ["Followup"]}},
            {"op_id": "7", "action": "mail.delete.soft", "drive_id": "me", "item_id": "msg-7", "args": {}},
        ],
    }))
    plan = load_plan(path)
    assert [op.action for op in plan.operations] == [
        "mail.move", "mail.copy", "mail.flag", "mail.read",
        "mail.focus", "mail.categorize", "mail.delete.soft",
    ]
