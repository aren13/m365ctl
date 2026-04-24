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
