"""Shared plan-file schema for mutating commands.

Every mutating CLI ( od-move, od-rename, od-copy, od-delete, od-clean,
od-label) can emit a plan file with ``--plan-out <path>`` and consume one
with ``--from-plan <path> --confirm``. The schema is fixed at this
version for the life of Plan 4; later plans bump ``PLAN_SCHEMA_VERSION``
and add a migration branch in ``load_plan``.

Key design choice: ``--from-plan`` operates on the exact ``item_id`` list
in the file. There is no glob re-expansion at execute time. See
``test_safety.py::test_from_plan_no_glob_reexpansion_exact_call_count``.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

PLAN_SCHEMA_VERSION = 1

Action = Literal[
    # Current, namespaced.
    "od.move", "od.rename", "od.copy", "od.delete", "od.restore",
    "od.label-apply", "od.label-remove", "od.download",
    "od.version-delete", "od.share-revoke", "od.recycle-purge",
    # Legacy bare — accepted on read for pre-refactor plans.
    "move", "rename", "copy", "delete", "restore",
    "label-apply", "label-remove", "download",
    "version-delete", "share-revoke", "recycle-purge",
    # Phase 2 — mail folder + category CRUD.
    "mail.folder.create", "mail.folder.rename",
    "mail.folder.move", "mail.folder.delete",
    "mail.categories.add", "mail.categories.update",
    "mail.categories.remove",
    # Phase 3 — safe message mutations.
    "mail.move", "mail.copy", "mail.flag", "mail.read",
    "mail.focus", "mail.categorize", "mail.delete.soft",
]

_VALID_ACTIONS: frozenset[str] = frozenset({
    # Current, namespaced.
    "od.move", "od.rename", "od.copy", "od.delete", "od.restore",
    "od.label-apply", "od.label-remove", "od.download",
    "od.version-delete", "od.share-revoke", "od.recycle-purge",
    # Legacy bare actions — accepted on read for pre-refactor plans; never
    # emitted by new code.
    "move", "rename", "copy", "delete", "restore",
    "label-apply", "label-remove", "download",
    "version-delete", "share-revoke", "recycle-purge",
    # Phase 2 — mail folder + category CRUD.
    "mail.folder.create", "mail.folder.rename",
    "mail.folder.move", "mail.folder.delete",
    "mail.categories.add", "mail.categories.update",
    "mail.categories.remove",
    # Phase 3 — safe message mutations.
    "mail.move", "mail.copy", "mail.flag", "mail.read",
    "mail.focus", "mail.categorize", "mail.delete.soft",
})

_REQUIRED_OP_FIELDS = ("op_id", "action", "drive_id", "item_id", "args")


class PlanFileError(ValueError):
    """Raised on malformed or unsupported plan files."""


@dataclass(frozen=True)
class Operation:
    op_id: str
    action: str
    drive_id: str
    item_id: str
    args: dict[str, Any]
    dry_run_result: str = ""


@dataclass(frozen=True)
class Plan:
    version: int
    created_at: str
    source_cmd: str
    scope: str
    operations: list[Operation] = field(default_factory=list)


def new_op_id() -> str:
    """Fresh per-op identifier — stdlib uuid4, lowercase hex, 36-char."""
    return str(uuid.uuid4())


def write_plan(plan: Plan, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": plan.version,
        "created_at": plan.created_at,
        "source_cmd": plan.source_cmd,
        "scope": plan.scope,
        "operations": [asdict(op) for op in plan.operations],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=False))


def load_plan(path: Path) -> Plan:
    try:
        raw = json.loads(Path(path).read_text())
    except FileNotFoundError as e:
        raise PlanFileError(f"plan file not found: {path}") from e
    except json.JSONDecodeError as e:
        raise PlanFileError(f"invalid JSON in {path}: {e}") from e

    version = raw.get("version")
    if version != PLAN_SCHEMA_VERSION:
        raise PlanFileError(
            f"unsupported plan version {version!r} (expected {PLAN_SCHEMA_VERSION})"
        )

    ops: list[Operation] = []
    for op_raw in raw.get("operations", []):
        for key in _REQUIRED_OP_FIELDS:
            if key not in op_raw:
                raise PlanFileError(
                    f"missing required op field {key!r} in operation {op_raw!r}"
                )
        if op_raw["action"] not in _VALID_ACTIONS:
            raise PlanFileError(f"unknown action {op_raw['action']!r}")
        ops.append(
            Operation(
                op_id=op_raw["op_id"],
                action=op_raw["action"],
                drive_id=op_raw["drive_id"],
                item_id=op_raw["item_id"],
                args=dict(op_raw.get("args", {})),
                dry_run_result=op_raw.get("dry_run_result", ""),
            )
        )
    return Plan(
        version=version,
        created_at=raw.get("created_at", ""),
        source_cmd=raw.get("source_cmd", ""),
        scope=raw.get("scope", ""),
        operations=ops,
    )
