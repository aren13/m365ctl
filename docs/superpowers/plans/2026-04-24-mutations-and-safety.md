# Mutations & Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship every mutating OneDrive/SharePoint operation (`od-move`, `od-rename`, `od-copy`, `od-delete`, `od-clean`, `od-label`, `od-undo`) behind a uniformly enforced safety envelope — dry-run default, plan-file workflow, scope allow/deny enforcement, append-only audit log, and reversible-where-possible undo — so that tenant-wide mutations are safe for Claude to drive and for humans to trust.

**Architecture:** Three new library modules under `m365ctl/`:

- `planfile.py` — schema + I/O for the shared JSON plan file consumed by `--from-plan` and emitted by `--plan-out`.
- `audit.py` — append-only JSONL writer for `logs/ops/YYYY-MM-DD.jsonl` with an `op_id` generator (stdlib `uuid.uuid4`) and a `log_mutation()` helper that every mutating command calls *before* hitting Graph.
- `safety.py` — `assert_scope_allowed()` that checks an item's drive against `config.scope.allow_drives`, its full path against `config.scope.deny_paths` (fnmatch glob), and enforces the `--unsafe-scope` + `/dev/tty` confirm escape hatch. Raises `ScopeViolation` on a rejected item.

Mutations themselves live under `m365ctl/mutate/` (one module per verb) and `m365ctl/cli/` (one file per command). Every verb follows the same pattern: **selection → filter-through-safety → plan build → (dry-run emit OR execute each op, logging before the Graph call, recording after)**. `with_retry` from Plan 2 is wired into a new `GraphClient.patch/post/delete` surface exclusively for mutating endpoints; read-endpoint auto-retry stays Plan 3's concern.

**Tech Stack:** Python 3.11+, `httpx`, `duckdb`, stdlib `uuid`, `fnmatch`, `json`; same `msal`-backed auth from Plan 1; PowerShell (`pwsh` + `PnP.PowerShell`) reused from Plan 3 for `od-label`.

**End-state (definition of done):**

- `./bin/od-move --pattern '**/*.tmp' --scope me --plan-out plan.json` writes a valid plan file and performs **zero** Graph mutations.
- `./bin/od-move --from-plan plan.json --confirm` executes exactly the ops in the file, with no glob re-expansion, appending one JSONL line per op to `logs/ops/YYYY-MM-DD.jsonl`.
- `./bin/od-rename`, `./bin/od-copy`, `./bin/od-delete`, `./bin/od-clean`, `./bin/od-label`, `./bin/od-undo` all exist, are `--confirm`-gated, and log to the audit trail.
- `tests/test_safety.py` verifies every spec §7 invariant (list below).
- One live smoke-test task round-trips a single file in the user's own OneDrive (rename → move → copy → recycle-delete → restore → recycle-delete again), observing matching audit-log entries.
- `git push` at the end of Task 13.

**Dependencies from Plans 1 and 2 (already in place):**

- `m365ctl.config.load_config` + `Config` + `ScopeConfig` (uses `allow_drives`, `deny_paths`, `unsafe_requires_flag`).
- `m365ctl.auth.AppOnlyCredential` / `DelegatedCredential`.
- `m365ctl.graph.GraphClient`, `GraphError`, `is_transient_graph_error`.
- `m365ctl.retry.with_retry`, `RetryExhausted`.
- `m365ctl.catalog.db.open_catalog` + `catalog.queries` (used for pattern expansion).
- `bin/` shell-wrapper pattern.

**Dependencies from Plan 3 (assumed landed before Plan 4 starts):**

- Tenant/site scope resolution. Plan 4 accepts `--scope tenant|site:<slug>` verbatim but the resolver lives in `m365ctl.catalog.crawl` (extended by Plan 3).
- PnP.PowerShell first-install workflow + cert-to-PFX conversion. Plan 4's `od-label` runs the same `pwsh` invocations but does not re-document the setup.
- Auto-retry wiring into `GraphClient.get` / `get_paginated`. Plan 4 adds `.patch`, `.post`, `.delete` and wires `with_retry` into them; read-endpoint retry remains Plan 3's responsibility.

**Intentionally deferred (not this plan — Plan 5):**

- `od-audit-sharing` rich reporting. Plan 5 ships the permissions/sharing audit suite.
- `od-sync-workspace` rclone bisync wrapper.
- `od-search` full-text merge with Graph `/search/query`. (Plan 3 ships filename-only search; the full-text tier is Plan 5.)
- MCP server. (Explicitly out-of-scope for phase 1.)
- Batched mutation via `$batch`. Plan 4 issues one request per op, which is simpler, lets the audit log interleave cleanly, and is fast enough for the tenant sizes we see (≤5k ops per plan).
- Cross-tenant copy. `od-copy` stays inside one tenant.

## Domain primer

### Graph mutation endpoints

All against base `https://graph.microsoft.com/v1.0`.

| Verb | Endpoint | Body |
|---|---|---|
| **Move**   | `PATCH /drives/{drive_id}/items/{item_id}` | `{"parentReference": {"id": "<new_parent_item_id>"}}` |
| **Rename** | `PATCH /drives/{drive_id}/items/{item_id}` | `{"name": "<new_name>"}` |
| **Copy**   | `POST  /drives/{drive_id}/items/{item_id}/copy` | `{"parentReference": {"driveId":"<id>","id":"<parent_item_id>"}, "name":"<new_name>"}`; returns 202 + `Location` header for async monitoring |
| **Delete (recycle)** | `DELETE /drives/{drive_id}/items/{item_id}` | _none_; 204 No Content; item goes to **recycle bin** (soft delete) |
| **Restore** | `POST /drives/{drive_id}/items/{item_id}/restore` | `{"parentReference": {"id": "<parent_item_id>"}}` (optional; defaults to original location) |
| **Recycle-bin purge** (hard delete) | `DELETE /drives/{drive_id}/items/{item_id}/permanentDelete` (preview) OR recycle-bin items under `/drives/{drive_id}/items/{item_id}` after locating them via `/drives/{drive_id}/recycleBin` | 204; **not reversible** |
| **List versions** | `GET /drives/{drive_id}/items/{item_id}/versions` | returns `{"value": [{"id":"...","lastModifiedDateTime":"..."},...]}` |
| **Delete version** | `DELETE /drives/{drive_id}/items/{item_id}/versions/{version_id}` | 204 |
| **List permissions** | `GET /drives/{drive_id}/items/{item_id}/permissions` | sharing links live here with `link.createdDateTime`, `link.scope`, `link.type` |
| **Delete permission** | `DELETE /drives/{drive_id}/items/{item_id}/permissions/{perm_id}` | revokes a sharing link |

`PATCH` = partial update; the body is a sparse JSON object with only the fields you want changed. `POST .../copy` is **asynchronous** — the response is 202 Accepted with a `Location` URL you poll for completion. For plan-file executions we poll until completion (or until a fixed 5-minute ceiling) so the audit log's `after` block can include the new item's id.

### Recycle-bin semantics

OneDrive `DELETE /drives/{id}/items/{iid}` is a **soft delete** — the item moves to the recycle bin and is restorable via `POST .../restore`. After the tenant retention policy elapses (default 93 days for OneDrive for Business), recycle-bin items are purged automatically. `od-clean recycle-bin` triggers that purge on demand for a given scope. Once purged, restoration requires Microsoft support and is out-of-scope for this toolkit — `od-undo` on a purged item emits a clear manual-restore instruction.

### Sensitivity-label taxonomy in the m365ctl tenant

Admin-defined label taxonomy is assumed already in place in Entra. `od-label` consumes label **names** (strings) as opaque identifiers and passes them to the PnP.PowerShell cmdlets `Set-PnPFileSensitivityLabel` / `Remove-PnPFileSensitivityLabel`. The toolkit does **not** define, edit, or enumerate labels; it only applies/removes them. The list of live labels is obtained (read-only) via `Get-PnPLabel` invoked by `od-label --list`. Four labels are expected in the m365ctl tenant at implementation time: `Public`, `Internal`, `Confidential`, `Highly Confidential`. Retention labels are applied with `Set-PnPFileComplianceLabel`; same input shape.

## File structure (new files in this plan)

```
src/m365ctl/
├── planfile.py                 # shared plan JSON schema + read/write
├── audit.py                    # append-only JSONL audit log
├── safety.py                   # scope allow/deny + TTY confirm gate
├── graph.py                    # MODIFIED: add patch/post/delete with with_retry
├── mutate/
│   ├── __init__.py
│   ├── move.py                 # move op against Graph + plan expansion
│   ├── rename.py
│   ├── copy.py                 # async copy w/ Location polling
│   ├── delete.py               # recycle delete + restore
│   ├── clean.py                # recycle-bin purge, old-versions, stale-shares
│   ├── label.py                # PnP.PowerShell dispatch
│   └── undo.py                 # inverse-op builder from audit log
└── cli/
    ├── move.py
    ├── rename.py
    ├── copy.py
    ├── delete.py
    ├── clean.py
    ├── label.py
    └── undo.py
bin/
├── od-move
├── od-rename
├── od-copy
├── od-delete
├── od-clean
├── od-label
└── od-undo
tests/
├── test_planfile.py
├── test_audit.py
├── test_safety.py              # THE adversarial tests; spec §7 coverage
├── test_graph_mutations.py     # patch/post/delete + retry wiring
├── test_mutate_move.py
├── test_mutate_rename.py
├── test_mutate_copy.py
├── test_mutate_delete.py
├── test_mutate_clean.py
├── test_mutate_label.py        # pwsh shelled out -> subprocess mocked
├── test_mutate_undo.py
├── test_cli_move.py
├── test_cli_rename.py
├── test_cli_copy.py
├── test_cli_delete.py
├── test_cli_clean.py
├── test_cli_label.py
└── test_cli_undo.py
```

## Spec §7 invariant → test coverage map

| Spec §7 rule | Where enforced | Test(s) that cover it |
|---|---|---|
| 1. Dry-run is the default; `--confirm` required to execute | every `cli/<verb>.py`: argparse default is `dry_run=True`; `--confirm` flips it | `test_safety.py::test_dry_run_is_default_no_mutation`, one per-verb in each `test_cli_*.py` |
| 2. Bulk destructive ops require a plan file | `cli/<verb>.py` rejects `--pattern ... --confirm` combo (no `--from-plan`) with exit-code 2 | `test_safety.py::test_pattern_plus_confirm_is_rejected` |
| 2. `--from-plan` does NOT re-expand globs | `mutate/<verb>.py` reads item_ids from plan; test wires a counting `httpx.MockTransport` | `test_safety.py::test_from_plan_no_glob_reexpansion_exact_call_count` |
| 3. Scope allow-list enforced | `safety.assert_scope_allowed` called before plan-file emit AND before each Graph call on execute | `test_safety.py::test_allow_drives_violation_raises`, `::test_allow_drives_unsafe_requires_tty_confirm` |
| 3. `--unsafe-scope` requires `/dev/tty` prompt; piped stdin cannot bypass | `safety._confirm_via_tty` opens `/dev/tty` directly (not `input()` / stdin) | `test_safety.py::test_piped_stdin_cannot_auto_confirm_unsafe_scope` |
| 4. Deny-paths absolute; never in dry-run output | `safety.filter_by_scope` drops them *before* plan emit; dry-run TSV and plan JSON both sourced from that filtered list | `test_safety.py::test_deny_paths_never_appear_in_plan_or_tsv` |
| 5. Every mutation writes to `logs/ops/YYYY-MM-DD.jsonl` | `audit.log_mutation_start()` called *before* the Graph call; `audit.log_mutation_end()` after (or in `except`) | `test_safety.py::test_audit_start_line_persists_even_on_mid_mutation_crash`, `test_audit.py::*` |
| 6. No hard deletes (recycle only) | `mutate.delete.recycle_delete()` uses `DELETE /drives/{d}/items/{i}` (soft); hard delete only reachable via `od-clean recycle-bin` | `test_mutate_delete.py::test_delete_routes_to_recycle_not_permadelete`, `test_mutate_clean.py::test_recycle_bin_purge_is_explicit_command` |
| 7. Rate-limit aware | `GraphClient.patch/post/delete` wrap the HTTP call in `with_retry(..., is_transient=is_transient_graph_error)` | `test_graph_mutations.py::test_patch_retries_on_429` |
| 8. `od-undo` replays reverse-ops; non-reversible flagged | `mutate.undo.build_reverse()` returns an inverse op record or raises `Irreversible` | `test_mutate_undo.py::*` (each op type + purged-recycle irreversible case) |

---

### Task 1: Plan-file schema + I/O (`planfile.py`)

**Files:**
- Create: `src/m365ctl/planfile.py`
- Create: `tests/test_planfile.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_planfile.py`:
```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from m365ctl.planfile import (
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
    from m365ctl.planfile import new_op_id
    a, b = new_op_id(), new_op_id()
    assert a != b
    assert len(a) == 36 and a.count("-") == 4
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_planfile.py -v
```
Expected: `ModuleNotFoundError: No module named 'm365ctl.planfile'`.

- [ ] **Step 3: Implement `planfile.py`**

Create `src/m365ctl/planfile.py`:
```python
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
    "move", "rename", "copy", "delete", "restore",
    "label-apply", "label-remove", "download",
    "version-delete", "share-revoke", "recycle-purge",
]

_VALID_ACTIONS: frozenset[str] = frozenset({
    "move", "rename", "copy", "delete", "restore",
    "label-apply", "label-remove", "download",
    "version-delete", "share-revoke", "recycle-purge",
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_planfile.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/planfile.py tests/test_planfile.py
git commit -m "feat(planfile): shared plan-file schema with JSON round-trip"
```

---

### Task 2: Audit log (`audit.py`)

**Files:**
- Create: `src/m365ctl/audit.py`
- Create: `tests/test_audit.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_audit.py`:
```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from m365ctl.audit import (
    AuditLogger,
    find_op_by_id,
    iter_audit_entries,
    log_mutation_end,
    log_mutation_start,
)


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "logs" / "ops")


def test_log_start_creates_jsonl_file(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    log_mutation_start(
        logger,
        op_id="op-1",
        cmd="od-rename",
        args={"new_name": "new.txt"},
        drive_id="d1",
        item_id="i1",
        before={"parent_path": "/", "name": "old.txt"},
    )
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / "logs" / "ops" / f"{day}.jsonl"
    assert f.exists()
    lines = f.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["op_id"] == "op-1"
    assert rec["cmd"] == "od-rename"
    assert rec["phase"] == "start"
    assert rec["before"] == {"parent_path": "/", "name": "old.txt"}


def test_log_start_then_end_writes_two_lines(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    log_mutation_start(
        logger, op_id="op-2", cmd="od-move", args={},
        drive_id="d", item_id="i",
        before={"parent_path": "/A", "name": "foo.txt"},
    )
    log_mutation_end(
        logger, op_id="op-2",
        after={"parent_path": "/B", "name": "foo.txt"},
        result="ok",
    )
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    recs = [
        json.loads(l)
        for l in (tmp_path / "logs" / "ops" / f"{day}.jsonl")
        .read_text()
        .strip()
        .splitlines()
    ]
    assert [r["phase"] for r in recs] == ["start", "end"]
    assert recs[1]["after"] == {"parent_path": "/B", "name": "foo.txt"}
    assert recs[1]["result"] == "ok"


def test_log_end_with_error(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    log_mutation_start(logger, op_id="op-3", cmd="od-move", args={},
                       drive_id="d", item_id="i",
                       before={"parent_path": "/", "name": "x"})
    log_mutation_end(logger, op_id="op-3", after=None, result="error",
                     error="HTTP403: forbidden")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = (tmp_path / "logs" / "ops" / f"{day}.jsonl").read_text().splitlines()
    rec = json.loads(lines[-1])
    assert rec["result"] == "error"
    assert rec["error"] == "HTTP403: forbidden"


def test_iter_audit_entries_reads_all_days(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    logger.ops_dir.mkdir(parents=True, exist_ok=True)
    (logger.ops_dir / "2026-04-23.jsonl").write_text(
        json.dumps({"op_id": "a", "phase": "start", "cmd": "od-move"}) + "\n"
        + json.dumps({"op_id": "a", "phase": "end", "result": "ok"}) + "\n"
    )
    (logger.ops_dir / "2026-04-24.jsonl").write_text(
        json.dumps({"op_id": "b", "phase": "start", "cmd": "od-rename"}) + "\n"
    )
    entries = list(iter_audit_entries(logger))
    op_ids = {e["op_id"] for e in entries}
    assert op_ids == {"a", "b"}


def test_find_op_by_id_returns_paired_records(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    log_mutation_start(logger, op_id="X", cmd="od-rename", args={"new_name": "n"},
                       drive_id="d", item_id="i",
                       before={"parent_path": "/", "name": "o.txt"})
    log_mutation_end(logger, op_id="X",
                     after={"parent_path": "/", "name": "n"}, result="ok")
    start, end = find_op_by_id(logger, "X")
    assert start["phase"] == "start"
    assert end["phase"] == "end"
    assert end["result"] == "ok"


def test_find_op_by_id_missing_returns_none(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    assert find_op_by_id(logger, "nope") == (None, None)


def test_audit_log_append_only(tmp_path: Path) -> None:
    """Writing to the same day re-appends; never truncates."""
    logger = _logger(tmp_path)
    for i in range(5):
        log_mutation_start(logger, op_id=f"op-{i}", cmd="od-move", args={},
                           drive_id="d", item_id=f"i{i}",
                           before={"parent_path": "/", "name": "x"})
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = (tmp_path / "logs" / "ops" / f"{day}.jsonl").read_text().splitlines()
    assert len(lines) == 5
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_audit.py -v
```
Expected: `ModuleNotFoundError: No module named 'm365ctl.audit'`.

- [ ] **Step 3: Implement `audit.py`**

Create `src/m365ctl/audit.py`:
```python
"""Append-only JSONL audit log for m365ctl mutations.

Spec §7 rule 5: every mutating command writes an entry BEFORE calling
Graph (phase='start') and a paired entry AFTER (phase='end'). The 'start'
record guarantees a trail even if the process crashes mid-mutation.

File layout: ``<ops_dir>/YYYY-MM-DD.jsonl``, one JSON object per line,
UTC-dated by when the entry is written.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class AuditLogger:
    """Bundles the on-disk ops directory.

    Pass one to every mutating function. The helpers below accept it as
    first positional argument so tests can use a tmp_path-rooted logger
    without monkey-patching any global state.
    """
    ops_dir: Path


def _today_path(logger: AuditLogger) -> Path:
    logger.ops_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return logger.ops_dir / f"{day}.jsonl"


def _append(logger: AuditLogger, record: dict[str, Any]) -> None:
    path = _today_path(logger)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")))
        f.write("\n")


def log_mutation_start(
    logger: AuditLogger,
    *,
    op_id: str,
    cmd: str,
    args: dict[str, Any],
    drive_id: str,
    item_id: str,
    before: dict[str, Any] | None,
) -> None:
    """Persist the 'I am about to do X' record BEFORE the Graph call."""
    _append(
        logger,
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "op_id": op_id,
            "phase": "start",
            "cmd": cmd,
            "args": args,
            "drive_id": drive_id,
            "item_id": item_id,
            "before": before,
        },
    )


def log_mutation_end(
    logger: AuditLogger,
    *,
    op_id: str,
    after: dict[str, Any] | None,
    result: str,
    error: str | None = None,
) -> None:
    """Persist the 'I finished / failed' record AFTER the Graph call."""
    _append(
        logger,
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "op_id": op_id,
            "phase": "end",
            "after": after,
            "result": result,
            "error": error,
        },
    )


def iter_audit_entries(logger: AuditLogger) -> Iterator[dict[str, Any]]:
    """Yield every record from every YYYY-MM-DD.jsonl under ops_dir."""
    if not logger.ops_dir.exists():
        return
    for path in sorted(logger.ops_dir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                yield json.loads(line)


def find_op_by_id(
    logger: AuditLogger, op_id: str
) -> tuple[dict | None, dict | None]:
    """Return (start_record, end_record) for ``op_id``, either ``None`` if absent."""
    start: dict | None = None
    end: dict | None = None
    for rec in iter_audit_entries(logger):
        if rec.get("op_id") != op_id:
            continue
        if rec.get("phase") == "start":
            start = rec
        elif rec.get("phase") == "end":
            end = rec
    return start, end
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_audit.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/audit.py tests/test_audit.py
git commit -m "feat(audit): append-only JSONL ops log with start/end phases"
```

---

### Task 3: Safety module (`safety.py`) — allow/deny + TTY

**Files:**
- Create: `src/m365ctl/safety.py`
- Create partial test file (extended in Task 9): `tests/test_safety.py` (initial subset)

- [ ] **Step 1: Write failing tests (subset — full adversarial suite in Task 9)**

Create `tests/test_safety.py`:
```python
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from m365ctl.config import Config, ScopeConfig
from m365ctl.safety import (
    ScopeViolation,
    assert_scope_allowed,
    filter_by_scope,
)


@dataclass(frozen=True)
class _Item:
    drive_id: str
    item_id: str
    full_path: str
    name: str = ""


def _cfg(
    *,
    allow: list[str] = None,
    deny: list[str] = None,
    unsafe_requires_flag: bool = True,
    tmp_path: Path = None,
) -> Config:
    scope = ScopeConfig(
        allow_drives=allow or ["d1"],
        allow_users=["*"],
        deny_paths=deny or [],
        unsafe_requires_flag=unsafe_requires_flag,
    )
    # Only the .scope field matters here; stub the rest.
    from m365ctl.config import CatalogConfig, LoggingConfig
    return Config(
        tenant_id="t", client_id="c",
        cert_path=(tmp_path or Path("/tmp")) / "k",
        cert_public=(tmp_path or Path("/tmp")) / "c",
        default_auth="app-only",
        scope=scope,
        catalog=CatalogConfig(path=(tmp_path or Path("/tmp")) / "x.duckdb"),
        logging=LoggingConfig(ops_dir=(tmp_path or Path("/tmp")) / "logs"),
    )


def test_allow_drives_permits_listed_drive(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    item = _Item(drive_id="d1", item_id="i", full_path="/foo")
    assert_scope_allowed(item, cfg, unsafe_scope=False)  # no raise


def test_allow_drives_blocks_unlisted_drive(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    item = _Item(drive_id="OTHER", item_id="i", full_path="/foo")
    with pytest.raises(ScopeViolation, match="drive"):
        assert_scope_allowed(item, cfg, unsafe_scope=False)


def test_deny_paths_block_matching_item(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], deny=["/Confidential/**"], tmp_path=tmp_path)
    item = _Item(drive_id="d1", item_id="i", full_path="/Confidential/secret.docx")
    with pytest.raises(ScopeViolation, match="deny"):
        assert_scope_allowed(item, cfg, unsafe_scope=False)


def test_filter_by_scope_drops_denied_items(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], deny=["/HR/**"], tmp_path=tmp_path)
    items = [
        _Item(drive_id="d1", item_id="a", full_path="/Public/report.pdf"),
        _Item(drive_id="d1", item_id="b", full_path="/HR/salaries.xlsx"),
        _Item(drive_id="d1", item_id="c", full_path="/HR"),  # exact match to parent
    ]
    kept = list(filter_by_scope(items, cfg, unsafe_scope=False))
    assert [i.item_id for i in kept] == ["a"]


def test_filter_by_scope_drops_items_outside_allow_drives(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    items = [
        _Item(drive_id="d1",    item_id="a", full_path="/p"),
        _Item(drive_id="OTHER", item_id="b", full_path="/p"),
    ]
    kept = list(filter_by_scope(items, cfg, unsafe_scope=False))
    assert [i.item_id for i in kept] == ["a"]


def test_unsafe_scope_bypasses_allow_list_with_tty_yes(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    item = _Item(drive_id="OTHER", item_id="i", full_path="/foo")
    with patch("m365ctl.safety._confirm_via_tty", return_value=True):
        assert_scope_allowed(item, cfg, unsafe_scope=True)  # no raise


def test_unsafe_scope_without_tty_yes_still_raises(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    item = _Item(drive_id="OTHER", item_id="i", full_path="/foo")
    with patch("m365ctl.safety._confirm_via_tty", return_value=False):
        with pytest.raises(ScopeViolation, match="declined"):
            assert_scope_allowed(item, cfg, unsafe_scope=True)


def test_unsafe_scope_flag_required_per_config(tmp_path: Path) -> None:
    """If unsafe_requires_flag is True (default), passing unsafe_scope=False
    against an out-of-scope item always raises — no TTY prompt offered."""
    cfg = _cfg(allow=["d1"], unsafe_requires_flag=True, tmp_path=tmp_path)
    item = _Item(drive_id="OTHER", item_id="i", full_path="/foo")
    with patch("m365ctl.safety._confirm_via_tty") as m:
        with pytest.raises(ScopeViolation):
            assert_scope_allowed(item, cfg, unsafe_scope=False)
        m.assert_not_called()  # never prompted — flag required upfront
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_safety.py -v
```
Expected: `ModuleNotFoundError: No module named 'm365ctl.safety'`.

- [ ] **Step 3: Implement `safety.py`**

Create `src/m365ctl/safety.py`:
```python
"""Scope allow/deny enforcement + /dev/tty escape hatch.

Spec §7 rules 3 and 4 live here. Every mutating command calls
``assert_scope_allowed(item, cfg, unsafe_scope=...)`` for each target
before touching Graph. Bulk selections call ``filter_by_scope`` to drop
deny-path items *before* plan emission — deny-path items never appear in
``--plan-out`` output or in dry-run TSV.

The ``--unsafe-scope`` escape hatch is the only way to mutate an item
whose drive_id is not in ``allow_drives``. It additionally requires a
``y/N`` confirmation read from ``/dev/tty`` (not stdin). This closes the
loophole where Claude (or any agent) pipes 'y\\n' into the command's
stdin: ``/dev/tty`` bypasses the redirected stdin and talks to the
controlling terminal directly. If no TTY is attached the confirm returns
False and the op is rejected.
"""
from __future__ import annotations

import fnmatch
from typing import Iterable, Iterator, Protocol

from m365ctl.config import Config


class _HasScopeFields(Protocol):
    drive_id: str
    item_id: str
    full_path: str


class ScopeViolation(RuntimeError):
    """Raised when an item is outside allow_drives or matches deny_paths.

    Kill chain: caught by the CLI's top-level handler, which exits with
    code 2 and prints ``str(err)``. Not caught anywhere deeper.
    """


def _confirm_via_tty(prompt: str) -> bool:
    """Read y/N from /dev/tty directly. Returns False if no TTY.

    Opens /dev/tty read-write and prompts there; an agent piping into
    the command's stdin cannot intercept this.
    """
    try:
        with open("/dev/tty", "r+", encoding="utf-8") as tty:
            tty.write(prompt)
            tty.flush()
            answer = tty.readline().strip().lower()
            return answer in {"y", "yes"}
    except OSError:
        return False


def _drive_allowed(item: _HasScopeFields, cfg: Config) -> bool:
    # Plan 4 keeps allow_drives a plain string set. Plan 3 introduces the
    # "me" and "site:<slug>" synonyms into the config; here we treat the
    # raw string value equality only. Scope resolution before us already
    # collapsed friendly names to drive ids where applicable.
    return item.drive_id in cfg.scope.allow_drives


def _deny_match(item: _HasScopeFields, cfg: Config) -> str | None:
    for pattern in cfg.scope.deny_paths:
        if fnmatch.fnmatch(item.full_path, pattern):
            return pattern
    return None


def assert_scope_allowed(
    item: _HasScopeFields,
    cfg: Config,
    *,
    unsafe_scope: bool,
) -> None:
    """Raise ``ScopeViolation`` unless the item is allowed.

    - Deny paths ALWAYS block, even with ``--unsafe-scope``.
    - Drive-not-in-allow-list blocks unless ``unsafe_scope=True`` AND the
      /dev/tty prompt confirms.
    """
    denied = _deny_match(item, cfg)
    if denied is not None:
        raise ScopeViolation(
            f"deny-path match: {item.full_path!r} matches {denied!r} "
            f"(deny_paths are absolute — no override)"
        )

    if _drive_allowed(item, cfg):
        return

    if not unsafe_scope:
        raise ScopeViolation(
            f"drive {item.drive_id!r} not in scope.allow_drives; "
            f"pass --unsafe-scope to override (requires TTY confirm)"
        )

    prompt = (
        f"UNSAFE SCOPE: drive {item.drive_id!r} is outside allow_drives.\n"
        f"  item full_path: {item.full_path!r}\n"
        f"Proceed anyway? [y/N]: "
    )
    if not _confirm_via_tty(prompt):
        raise ScopeViolation(
            f"user declined /dev/tty confirm for unsafe-scope item "
            f"drive={item.drive_id!r} path={item.full_path!r}"
        )


def filter_by_scope(
    items: Iterable[_HasScopeFields],
    cfg: Config,
    *,
    unsafe_scope: bool,
) -> Iterator[_HasScopeFields]:
    """Drop items that would raise ``ScopeViolation``.

    Used during bulk selection (before plan emission) so deny-path items
    never appear in the plan. An allow-list miss with ``unsafe_scope=True``
    still prompts once per item; pass the whole selection through
    ``assert_scope_allowed`` at execute time anyway — this filter is a
    fast pre-pass, not the authoritative gate.
    """
    for item in items:
        if _deny_match(item, cfg) is not None:
            continue
        if _drive_allowed(item, cfg):
            yield item
            continue
        if not unsafe_scope:
            continue
        # Prompt once per item. Order matches the source iterable.
        try:
            assert_scope_allowed(item, cfg, unsafe_scope=True)
        except ScopeViolation:
            continue
        yield item
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_safety.py -v
```
Expected: 8 passed. (The remaining adversarial tests land in Task 9.)

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/safety.py tests/test_safety.py
git commit -m "feat(safety): scope allow/deny guardrail with /dev/tty confirm"
```

---

### Task 4: Extend `GraphClient` with mutation verbs + retry

**Files:**
- Modify: `src/m365ctl/graph.py`
- Create: `tests/test_graph_mutations.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_graph_mutations.py`:
```python
from __future__ import annotations

import httpx
import pytest

from m365ctl.graph import GraphClient, GraphError


def test_patch_sends_json_body_with_bearer() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = request.content.decode()
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"id": "i1", "name": "new.txt"})

    client = GraphClient(
        token_provider=lambda: "tkn",
        transport=httpx.MockTransport(handler),
    )
    body = client.patch("/drives/d1/items/i1", json_body={"name": "new.txt"})

    assert seen["method"] == "PATCH"
    assert '"name":"new.txt"' in seen["body"].replace(" ", "")
    assert seen["auth"] == "Bearer tkn"
    assert body["id"] == "i1"


def test_post_sends_body_and_returns_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            202,
            headers={"Location": "https://graph/copy-monitor/abc"},
            json={},
        )

    client = GraphClient(
        token_provider=lambda: "tkn",
        transport=httpx.MockTransport(handler),
    )
    resp = client.post_raw(
        "/drives/d1/items/i1/copy",
        json_body={"parentReference": {"id": "p"}},
    )
    assert resp.status_code == 202
    assert resp.headers["location"] == "https://graph/copy-monitor/abc"


def test_delete_returns_none_on_204() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    client = GraphClient(
        token_provider=lambda: "tkn",
        transport=httpx.MockTransport(handler),
    )
    assert client.delete("/drives/d1/items/i1") is None


def test_patch_raises_graph_error_on_4xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"error": {"code": "accessDenied", "message": "nope"}}
        )

    client = GraphClient(
        token_provider=lambda: "tkn",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(GraphError, match="accessDenied"):
        client.patch("/drives/d1/items/i1", json_body={"name": "x"})


def test_patch_retries_on_429_then_succeeds() -> None:
    """Confirms the mutation verbs are wrapped in with_retry."""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                json={"error": {"code": "TooManyRequests", "message": "slow"}},
            )
        return httpx.Response(200, json={"id": "i1"})

    client = GraphClient(
        token_provider=lambda: "tkn",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,  # zero delay in tests
    )
    result = client.patch("/drives/d1/items/i1", json_body={"name": "y"})
    assert result == {"id": "i1"}
    assert attempts["n"] == 3


def test_patch_gives_up_after_max_attempts() -> None:
    from m365ctl.retry import RetryExhausted

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"error": {"code": "serviceNotAvailable", "message": "down"}},
        )

    client = GraphClient(
        token_provider=lambda: "tkn",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
        max_retry_attempts=3,
    )
    with pytest.raises(RetryExhausted):
        client.patch("/drives/d1/items/i1", json_body={"name": "y"})
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_graph_mutations.py -v
```
Expected: errors on `.patch`, `.delete`, `.post_raw`, `sleep=`, `max_retry_attempts=` — the attributes don't exist yet.

- [ ] **Step 3: Extend `graph.py`**

Replace `src/m365ctl/graph.py` with:
```python
"""Thin httpx-backed Microsoft Graph client.

Plan 1 covered single-call GETs; Plan 2 added pagination + transient
classification; Plan 4 adds mutation verbs (``patch``, ``post_raw``,
``delete``) with automatic retry on transient errors. Read endpoints
(``get``, ``get_paginated``) are wired for retry in Plan 3.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Iterator

import httpx

from m365ctl.retry import with_retry

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_TRANSIENT_CODES = {
    "TooManyRequests",
    "serviceNotAvailable",
    "HTTP429",
    "HTTP500",
    "HTTP502",
    "HTTP503",
    "HTTP504",
}


class GraphError(RuntimeError):
    """Raised when Graph returns a non-2xx response.

    The first colon-separated token of ``str(err)`` is the Graph error code
    (or ``HTTP<status>`` fallback); use ``is_transient_graph_error`` to
    classify. Mutation-verb helpers store the raw ``Retry-After`` hint (if
    any) on the attribute ``retry_after`` for the retry helper.
    """

    retry_after: float | None = None


def is_transient_graph_error(exc: Exception) -> bool:
    if not isinstance(exc, GraphError):
        return False
    head = str(exc).split(":", 1)[0].strip()
    return head in _TRANSIENT_CODES


def _retry_after_of(exc: Exception) -> float | None:
    if isinstance(exc, GraphError):
        return exc.retry_after
    return None


class GraphClient:
    def __init__(
        self,
        *,
        token_provider: Callable[[], str],
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        max_retry_attempts: int = 5,
    ) -> None:
        self._token_provider = token_provider
        self._client = httpx.Client(
            base_url=GRAPH_BASE,
            transport=transport,
            timeout=timeout,
        )
        self._sleep = sleep
        self._max_retry_attempts = max_retry_attempts

    # ------------------------------------------------------------------ reads

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token_provider()}"}

    def get(self, path: str, *, params: dict | None = None) -> dict:
        resp = self._client.get(path, headers=self._auth_headers(), params=params)
        return self._parse(resp)

    def get_absolute(self, url: str) -> dict:
        resp = self._client.get(url, headers=self._auth_headers())
        return self._parse(resp)

    def get_paginated(
        self, path: str, *, params: dict | None = None
    ) -> Iterator[tuple[list[dict], str | None]]:
        body = self.get(path, params=params)
        while True:
            items = body.get("value", [])
            next_link = body.get("@odata.nextLink")
            delta_link = body.get("@odata.deltaLink")
            yield items, delta_link
            if not next_link:
                return
            body = self.get_absolute(next_link)

    # -------------------------------------------------------------- mutations

    def patch(self, path: str, *, json_body: dict[str, Any]) -> dict:
        """Graph PATCH with JSON body, retry on transient errors."""
        def do() -> dict:
            resp = self._client.patch(
                path, headers=self._auth_headers(), json=json_body
            )
            return self._parse(resp)
        return self._with_retry(do)

    def post_raw(
        self, path: str, *, json_body: dict[str, Any] | None = None
    ) -> httpx.Response:
        """POST returning the raw response (caller inspects Location etc.)."""
        def do() -> httpx.Response:
            resp = self._client.post(
                path, headers=self._auth_headers(), json=json_body
            )
            self._maybe_raise(resp)
            return resp
        return self._with_retry(do)

    def delete(self, path: str) -> None:
        """Graph DELETE; returns None on 204."""
        def do() -> None:
            resp = self._client.delete(path, headers=self._auth_headers())
            if resp.status_code == 204:
                return None
            # Some endpoints (e.g. permanentDelete) return 200 + body.
            self._parse(resp)
            return None
        return self._with_retry(do)

    # ------------------------------------------------------------ internals

    def _with_retry(self, do):
        return with_retry(
            do,
            max_attempts=self._max_retry_attempts,
            sleep=self._sleep,
            is_transient=is_transient_graph_error,
            retry_after_of=_retry_after_of,
        )

    def _maybe_raise(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            body = resp.json() if resp.content else {}
            err = body.get("error", {})
            code = err.get("code", f"HTTP{resp.status_code}")
            msg = err.get("message", resp.text[:200])
            e = GraphError(f"{code}: {msg}")
            ra = resp.headers.get("Retry-After")
            if ra is not None:
                try:
                    e.retry_after = float(ra)
                except ValueError:
                    pass
            raise e

    def _parse(self, resp: httpx.Response) -> dict:
        self._maybe_raise(resp)
        return resp.json() if resp.content else {}

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_graph.py tests/test_graph_pagination.py tests/test_graph_mutations.py -v
```
Expected: 11 passed (2 + 3 + 6).

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/graph.py tests/test_graph_mutations.py
git commit -m "feat(graph): patch/post_raw/delete verbs with automatic retry"
```

---

### Task 5: `mutate/move.py` + `mutate/rename.py` + CLI (`od-move`, `od-rename`)

**Files:**
- Create: `src/m365ctl/mutate/__init__.py` (empty)
- Create: `src/m365ctl/mutate/move.py`
- Create: `src/m365ctl/mutate/rename.py`
- Create: `src/m365ctl/cli/move.py`
- Create: `src/m365ctl/cli/rename.py`
- Create: `tests/test_mutate_move.py`
- Create: `tests/test_mutate_rename.py`
- Create: `tests/test_cli_move.py`
- Create: `tests/test_cli_rename.py`
- Modify: `src/m365ctl/cli/__main__.py`

- [ ] **Step 1: Create the package**

```bash
mkdir -p src/m365ctl/mutate
touch src/m365ctl/mutate/__init__.py
```

- [ ] **Step 2: Write failing tests for `mutate/move.py`**

Create `tests/test_mutate_move.py`:
```python
from __future__ import annotations

import httpx

from m365ctl.audit import AuditLogger, iter_audit_entries
from m365ctl.mutate.move import execute_move
from m365ctl.planfile import Operation


def _op(**over) -> Operation:
    base = dict(
        op_id="op-1", action="move", drive_id="d1", item_id="i1",
        args={"new_parent_item_id": "NEWPARENT"},
        dry_run_result="would move /A/x -> /B/x",
    )
    base.update(over)
    return Operation(**base)


def test_execute_move_issues_patch_and_logs_both_phases(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content.decode()))
        return httpx.Response(
            200,
            json={
                "id": "i1", "name": "x",
                "parentReference": {"id": "NEWPARENT", "path": "/drive/root:/B"},
            },
        )

    from m365ctl.graph import GraphClient
    client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    result = execute_move(
        _op(),
        client,
        logger,
        before={"parent_path": "/A", "name": "x"},
    )

    assert result.status == "ok"
    assert calls[0][0] == "PATCH"
    assert "NEWPARENT" in calls[0][2]

    entries = list(iter_audit_entries(logger))
    phases = [e["phase"] for e in entries if e["op_id"] == "op-1"]
    assert phases == ["start", "end"]


def test_execute_move_start_record_persists_even_if_graph_raises(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"error": {"code": "accessDenied", "message": "no"}}
        )

    from m365ctl.graph import GraphClient
    client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    result = execute_move(_op(), client, logger,
                          before={"parent_path": "/A", "name": "x"})
    assert result.status == "error"
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-1"]
    assert [e["phase"] for e in entries] == ["start", "end"]
    assert entries[1]["result"] == "error"
    assert "accessDenied" in entries[1]["error"]
```

- [ ] **Step 3: Implement `mutate/move.py`**

Create `src/m365ctl/mutate/move.py`:
```python
"""OneDrive MOVE via Graph PATCH .../items/{id}.

A MOVE is a PATCH with a sparse ``parentReference.id`` body. The item_id
does not change across a move — handy for audit log idempotency and
undo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from m365ctl.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.graph import GraphClient, GraphError
from m365ctl.planfile import Operation


@dataclass(frozen=True)
class MoveResult:
    op_id: str
    status: str  # "ok" | "error"
    error: str | None = None
    after: dict[str, Any] | None = None


def execute_move(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MoveResult:
    """Execute a single move op, logging start BEFORE, end AFTER.

    Spec §7 rule 5 invariant: the 'start' record is persisted before the
    Graph call, so a crash mid-call still leaves a trail. We wrap the
    Graph call in try/except to guarantee a matching 'end' record.
    """
    new_parent = op.args["new_parent_item_id"]
    log_mutation_start(
        logger, op_id=op.op_id, cmd="od-move",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id,
        before=before,
    )
    try:
        result = graph.patch(
            f"/drives/{op.drive_id}/items/{op.item_id}",
            json_body={"parentReference": {"id": new_parent}},
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return MoveResult(op_id=op.op_id, status="error", error=str(e))
    after = {
        "parent_path": (result.get("parentReference") or {}).get("path", ""),
        "name": result.get("name", before.get("name", "")),
        "parent_id": new_parent,
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MoveResult(op_id=op.op_id, status="ok", after=after)
```

- [ ] **Step 4: Implement `mutate/rename.py` analogously**

Create `tests/test_mutate_rename.py`:
```python
from __future__ import annotations

import httpx

from m365ctl.audit import AuditLogger, iter_audit_entries
from m365ctl.graph import GraphClient
from m365ctl.mutate.rename import execute_rename
from m365ctl.planfile import Operation


def test_execute_rename_issues_patch_with_new_name(tmp_path):
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "i1", "name": "new.txt"})

    client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-1", action="rename", drive_id="d1", item_id="i1",
                   args={"new_name": "new.txt"}, dry_run_result="")
    result = execute_rename(op, client, logger,
                            before={"parent_path": "/", "name": "old.txt"})
    assert result.status == "ok"
    assert '"name":"new.txt"' in captured["body"].replace(" ", "")
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-1"]
    assert entries[-1]["after"]["name"] == "new.txt"
```

Create `src/m365ctl/mutate/rename.py`:
```python
"""OneDrive RENAME via Graph PATCH .../items/{id} with {'name': ...}."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from m365ctl.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.graph import GraphClient, GraphError
from m365ctl.planfile import Operation


@dataclass(frozen=True)
class RenameResult:
    op_id: str
    status: str
    error: str | None = None
    after: dict[str, Any] | None = None


def execute_rename(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> RenameResult:
    new_name = op.args["new_name"]
    log_mutation_start(
        logger, op_id=op.op_id, cmd="od-rename",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id,
        before=before,
    )
    try:
        result = graph.patch(
            f"/drives/{op.drive_id}/items/{op.item_id}",
            json_body={"name": new_name},
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return RenameResult(op_id=op.op_id, status="error", error=str(e))
    after = {
        "parent_path": before.get("parent_path", ""),
        "name": result.get("name", new_name),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return RenameResult(op_id=op.op_id, status="ok", after=after)
```

- [ ] **Step 5: Run the mutate tests**

```bash
uv run pytest tests/test_mutate_move.py tests/test_mutate_rename.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Write CLI tests for `od-move` and `od-rename`**

Both CLIs share the shape: selection → safety filter → plan build → emit OR execute. Create `tests/test_cli_move.py`:
```python
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from m365ctl.cli.move import run_move


def _stub_cfg(tmp_path: Path, *, allow=None, deny=None):
    from m365ctl.config import CatalogConfig, Config, LoggingConfig, ScopeConfig
    return Config(
        tenant_id="t", client_id="c",
        cert_path=tmp_path / "k", cert_public=tmp_path / "c",
        default_auth="app-only",
        scope=ScopeConfig(
            allow_drives=allow or ["d1"],
            allow_users=["*"],
            deny_paths=deny or [],
            unsafe_requires_flag=True,
        ),
        catalog=CatalogConfig(path=tmp_path / "catalog.duckdb"),
        logging=LoggingConfig(ops_dir=tmp_path / "logs/ops"),
    )


def test_single_item_dry_run_does_not_call_graph(tmp_path, mocker, capsys):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.cli.move.load_config", return_value=cfg)
    # Fake Graph: assert never called.
    mock_client = MagicMock()
    mocker.patch("m365ctl.cli.move.build_graph_client", return_value=mock_client)
    # Target lookup: stub the helper that resolves item metadata.
    mocker.patch(
        "m365ctl.cli.move._lookup_item",
        return_value={"drive_id": "d1", "item_id": "i1",
                      "full_path": "/A/x", "name": "x",
                      "parent_path": "/A"},
    )

    rc = run_move(
        config_path=tmp_path / "config.toml",
        scope="drive:d1",
        item_id="i1",
        drive_id="d1",
        pattern=None,
        from_plan=None,
        new_parent_path="/B",
        new_parent_item_id="PID-B",
        plan_out=None,
        confirm=False,
        unsafe_scope=False,
    )
    assert rc == 0
    mock_client.patch.assert_not_called()
    out = capsys.readouterr().out
    assert "DRY-RUN" in out or "would move" in out.lower()


def test_pattern_plus_confirm_rejected_without_from_plan(tmp_path, mocker, capsys):
    """Spec §7 rule 2: bulk destructive requires a plan file."""
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.cli.move.load_config", return_value=cfg)
    rc = run_move(
        config_path=tmp_path / "config.toml",
        scope="drive:d1", item_id=None, drive_id=None,
        pattern="**/*.tmp",
        from_plan=None,
        new_parent_path="/Trash", new_parent_item_id="TRASH",
        plan_out=None, confirm=True, unsafe_scope=False,
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "plan" in err.lower()


def test_from_plan_issues_exactly_one_patch_per_op(tmp_path, mocker):
    """Counting mock transport — proves no glob re-expansion."""
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.cli.move.load_config", return_value=cfg)

    calls = {"n": 0}
    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200, json={"id": "ignored",
                       "parentReference": {"id": "P", "path": "/B"},
                       "name": "x"})

    from m365ctl.graph import GraphClient
    real_client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    mocker.patch("m365ctl.cli.move.build_graph_client", return_value=real_client)
    mocker.patch(
        "m365ctl.cli.move._lookup_item",
        side_effect=lambda graph, drive_id, item_id: {
            "drive_id": drive_id, "item_id": item_id,
            "full_path": f"/src/{item_id}", "name": item_id,
            "parent_path": "/src",
        },
    )

    # Write a plan with exactly 3 operations.
    from m365ctl.planfile import PLAN_SCHEMA_VERSION
    plan = {
        "version": PLAN_SCHEMA_VERSION,
        "created_at": "2026-04-24T10:00:00+00:00",
        "source_cmd": "od-move --pattern ...",
        "scope": "drive:d1",
        "operations": [
            {"op_id": f"op-{i}", "action": "move",
             "drive_id": "d1", "item_id": f"I{i}",
             "args": {"new_parent_item_id": "P"},
             "dry_run_result": ""} for i in range(3)
        ],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))

    rc = run_move(
        config_path=tmp_path / "config.toml",
        scope=None, item_id=None, drive_id=None, pattern=None,
        from_plan=plan_path,
        new_parent_path=None, new_parent_item_id=None,
        plan_out=None, confirm=True, unsafe_scope=False,
    )
    assert rc == 0
    assert calls["n"] == 3  # exactly one PATCH per op, no expansion
```

Create `tests/test_cli_rename.py`:
```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.cli.rename import run_rename


def _stub_cfg(tmp_path: Path):
    from m365ctl.config import CatalogConfig, Config, LoggingConfig, ScopeConfig
    return Config(
        tenant_id="t", client_id="c",
        cert_path=tmp_path / "k", cert_public=tmp_path / "c",
        default_auth="app-only",
        scope=ScopeConfig(allow_drives=["d1"], allow_users=["*"],
                          deny_paths=[], unsafe_requires_flag=True),
        catalog=CatalogConfig(path=tmp_path / "catalog.duckdb"),
        logging=LoggingConfig(ops_dir=tmp_path / "logs/ops"),
    )


def test_single_rename_dry_run_no_graph_call(tmp_path, mocker, capsys):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.cli.rename.load_config", return_value=cfg)
    mocker.patch(
        "m365ctl.cli.rename._lookup_item",
        return_value={"drive_id": "d1", "item_id": "i1",
                      "full_path": "/x.txt", "name": "x.txt",
                      "parent_path": "/"},
    )
    client = MagicMock()
    mocker.patch("m365ctl.cli.rename.build_graph_client", return_value=client)

    rc = run_rename(
        config_path=tmp_path / "config.toml",
        scope="drive:d1",
        drive_id="d1", item_id="i1",
        new_name="y.txt",
        from_plan=None, plan_out=None,
        confirm=False, unsafe_scope=False,
    )
    assert rc == 0
    client.patch.assert_not_called()
    assert "DRY-RUN" in capsys.readouterr().out
```

- [ ] **Step 7: Implement `cli/move.py` and `cli/rename.py`**

Create a shared helper file `src/m365ctl/cli/_common.py`:
```python
"""Shared helpers for mutating CLIs.

- ``build_graph_client``: config -> GraphClient (picks credential by scope)
- ``expand_pattern``: fnmatch against the catalog to produce candidate items
- ``require_plan_for_bulk``: spec §7 rule 2 guard
- ``emit_plan_or_tsv``: dry-run output
"""
from __future__ import annotations

import fnmatch
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from m365ctl.auth import AppOnlyCredential, DelegatedCredential
from m365ctl.catalog.db import open_catalog
from m365ctl.config import Config
from m365ctl.graph import GraphClient
from m365ctl.planfile import PLAN_SCHEMA_VERSION, Operation, Plan, write_plan


@dataclass(frozen=True)
class CandidateItem:
    drive_id: str
    item_id: str
    full_path: str
    name: str
    parent_path: str


def build_graph_client(cfg: Config, scope: str | None) -> GraphClient:
    cred = (
        DelegatedCredential(cfg) if scope == "me"
        else AppOnlyCredential(cfg)
    )
    token = cred.get_token()
    return GraphClient(token_provider=lambda: token)


def expand_pattern(
    cfg: Config,
    pattern: str,
    scope_drive_ids: list[str] | None = None,
) -> Iterator[CandidateItem]:
    """Match ``pattern`` (fnmatch) against item full_paths in the catalog.

    The catalog is the only source of truth for bulk selection — we do
    NOT live-list from Graph here. This means users must
    ``od-catalog-refresh`` before a bulk mutation; a stale catalog
    surfaces as 'item not found' errors at execute time, not as wrong
    deletes (spec §5 consistency model).
    """
    with open_catalog(cfg.catalog.path) as conn:
        where = "is_folder = false AND is_deleted = false"
        params: list = []
        if scope_drive_ids:
            where += " AND drive_id = ANY(?)"
            params.append(scope_drive_ids)
        cur = conn.execute(
            f"SELECT drive_id, item_id, full_path, name, parent_path "
            f"FROM items WHERE {where}",
            params,
        )
        for drive_id, item_id, full_path, name, parent_path in cur.fetchall():
            if fnmatch.fnmatch(full_path, pattern):
                yield CandidateItem(drive_id, item_id, full_path, name,
                                    parent_path)


def require_plan_for_bulk(
    *, pattern: str | None, from_plan: Path | None,
    confirm: bool, cmd_name: str,
) -> int:
    """Spec §7 rule 2: patterns + --confirm without --from-plan is rejected."""
    if pattern is not None and confirm and not from_plan:
        print(
            f"{cmd_name}: bulk selection ({pattern!r}) requires the plan-file "
            "workflow. Generate a plan with --plan-out, review it, then "
            "execute with --from-plan --confirm.",
            file=sys.stderr,
        )
        return 2
    return 0


def emit_plan(
    plan: Plan,
    *,
    plan_out: Path | None,
) -> None:
    if plan_out is not None:
        write_plan(plan, plan_out)
        print(f"Wrote plan: {plan_out}  ({len(plan.operations)} ops)")
        return
    # TSV to stdout, deny-paths already filtered upstream
    print("DRY-RUN — no mutations applied. Re-run with --plan-out for full JSON.")
    print("op_id\taction\tdrive_id\titem_id\tdry_run_result")
    for op in plan.operations:
        print(f"{op.op_id}\t{op.action}\t{op.drive_id}\t"
              f"{op.item_id}\t{op.dry_run_result}")


def new_plan(*, source_cmd: str, scope: str,
             operations: list[Operation]) -> Plan:
    return Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(),
        source_cmd=source_cmd,
        scope=scope,
        operations=operations,
    )
```

Create `src/m365ctl/cli/move.py`:
```python
"""`od-move` — move items between parents in OneDrive.

Selection: --item-id/--drive-id (single), --pattern (bulk, catalog-backed),
or --from-plan. Dry-run by default; --confirm required to execute.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.audit import AuditLogger
from m365ctl.cli._common import (
    CandidateItem,
    build_graph_client,
    emit_plan,
    expand_pattern,
    new_plan,
    require_plan_for_bulk,
)
from m365ctl.config import load_config
from m365ctl.mutate.move import execute_move
from m365ctl.planfile import Operation, load_plan, new_op_id
from m365ctl.safety import ScopeViolation, assert_scope_allowed, filter_by_scope


def _lookup_item(graph, drive_id: str, item_id: str) -> dict:
    meta = graph.get(f"/drives/{drive_id}/items/{item_id}")
    parent_path = ((meta.get("parentReference") or {}).get("path") or "")
    if parent_path.startswith("/drive/root:"):
        parent_path = parent_path[len("/drive/root:"):] or "/"
    full_path = (
        meta["name"] if parent_path == "/"
        else f"{parent_path}/{meta['name']}"
    )
    return {
        "drive_id": drive_id,
        "item_id": item_id,
        "full_path": full_path,
        "name": meta["name"],
        "parent_path": parent_path,
    }


def run_move(
    *,
    config_path: Path,
    scope: str | None,
    drive_id: str | None,
    item_id: str | None,
    pattern: str | None,
    from_plan: Path | None,
    new_parent_path: str | None,
    new_parent_item_id: str | None,
    plan_out: Path | None,
    confirm: bool,
    unsafe_scope: bool,
) -> int:
    cfg = load_config(config_path)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)

    # Spec §7 rule 2: bulk destructive requires a plan file.
    rc = require_plan_for_bulk(
        pattern=pattern, from_plan=from_plan, confirm=confirm,
        cmd_name="od-move",
    )
    if rc:
        return rc

    # Execute path: --from-plan + --confirm
    if from_plan is not None:
        if not confirm:
            print("od-move --from-plan requires --confirm.", file=sys.stderr)
            return 2
        plan = load_plan(from_plan)
        graph = build_graph_client(cfg, plan.scope)
        any_error = False
        for op in plan.operations:
            if op.action != "move":
                continue
            before_meta = _lookup_item(graph, op.drive_id, op.item_id)
            try:
                assert_scope_allowed(
                    type("X", (), before_meta)(), cfg, unsafe_scope=unsafe_scope
                )
            except ScopeViolation as e:
                print(f"[{op.op_id}] skipped: {e}", file=sys.stderr)
                any_error = True
                continue
            result = execute_move(op, graph, logger,
                                  before={"parent_path": before_meta["parent_path"],
                                          "name": before_meta["name"]})
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    # Dry-run / single execute path
    if item_id is not None and drive_id is not None:
        graph = build_graph_client(cfg, scope)
        meta = _lookup_item(graph, drive_id, item_id)
        candidates: list[CandidateItem] = [
            CandidateItem(**meta)
        ]
    elif pattern is not None:
        if scope is None:
            print("od-move --pattern requires --scope", file=sys.stderr)
            return 2
        candidates = list(expand_pattern(cfg, pattern))
    else:
        print(
            "od-move: provide --item-id/--drive-id, --pattern, or --from-plan",
            file=sys.stderr,
        )
        return 2

    if new_parent_item_id is None:
        print("od-move: --new-parent-item-id is required to build a plan",
              file=sys.stderr)
        return 2

    # Safety filter (deny-paths dropped; allow-list enforced).
    kept = list(filter_by_scope(candidates, cfg, unsafe_scope=unsafe_scope))

    ops = [
        Operation(
            op_id=new_op_id(),
            action="move",
            drive_id=item.drive_id,
            item_id=item.item_id,
            args={
                "new_parent_item_id": new_parent_item_id,
                "new_parent_path": new_parent_path or "",
            },
            dry_run_result=f"would move {item.full_path} -> "
                           f"{new_parent_path or new_parent_item_id}/{item.name}",
        )
        for item in kept
    ]
    src = (
        f"od-move --pattern {pattern!r} --scope {scope}" if pattern
        else f"od-move --item-id {item_id} --drive-id {drive_id}"
    )
    plan = new_plan(source_cmd=src, scope=scope or "", operations=ops)

    if confirm and pattern is None:
        # Single-item --confirm path (no --from-plan required per spec rule 2).
        graph = build_graph_client(cfg, scope)
        any_error = False
        for op in plan.operations:
            meta = _lookup_item(graph, op.drive_id, op.item_id)
            result = execute_move(op, graph, logger,
                                  before={"parent_path": meta["parent_path"],
                                          "name": meta["name"]})
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    emit_plan(plan, plan_out=plan_out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-move")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--scope")
    p.add_argument("--drive-id")
    p.add_argument("--item-id")
    p.add_argument("--pattern")
    p.add_argument("--from-plan", type=Path)
    p.add_argument("--new-parent-path")
    p.add_argument("--new-parent-item-id")
    p.add_argument("--plan-out", type=Path)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--unsafe-scope", action="store_true")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_move(
        config_path=Path(args.config),
        scope=args.scope,
        drive_id=args.drive_id,
        item_id=args.item_id,
        pattern=args.pattern,
        from_plan=args.from_plan,
        new_parent_path=args.new_parent_path,
        new_parent_item_id=args.new_parent_item_id,
        plan_out=args.plan_out,
        confirm=args.confirm,
        unsafe_scope=args.unsafe_scope,
    )
```

Create `src/m365ctl/cli/rename.py` following the same pattern (single-item only; rename is never bulk in practice):
```python
"""`od-rename` — rename a single item (or a plan's worth)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.audit import AuditLogger
from m365ctl.cli._common import build_graph_client, emit_plan, new_plan
from m365ctl.cli.move import _lookup_item  # reuse
from m365ctl.config import load_config
from m365ctl.mutate.rename import execute_rename
from m365ctl.planfile import Operation, load_plan, new_op_id
from m365ctl.safety import ScopeViolation, assert_scope_allowed


def run_rename(
    *,
    config_path: Path,
    scope: str | None,
    drive_id: str | None,
    item_id: str | None,
    new_name: str | None,
    from_plan: Path | None,
    plan_out: Path | None,
    confirm: bool,
    unsafe_scope: bool,
) -> int:
    cfg = load_config(config_path)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)

    if from_plan is not None:
        if not confirm:
            print("od-rename --from-plan requires --confirm.", file=sys.stderr)
            return 2
        plan = load_plan(from_plan)
        graph = build_graph_client(cfg, plan.scope)
        any_error = False
        for op in plan.operations:
            if op.action != "rename":
                continue
            meta = _lookup_item(graph, op.drive_id, op.item_id)
            try:
                assert_scope_allowed(type("X", (), meta)(), cfg,
                                     unsafe_scope=unsafe_scope)
            except ScopeViolation as e:
                print(f"[{op.op_id}] skipped: {e}", file=sys.stderr)
                any_error = True
                continue
            result = execute_rename(op, graph, logger,
                                    before={"parent_path": meta["parent_path"],
                                            "name": meta["name"]})
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    if not (item_id and drive_id and new_name):
        print("od-rename: provide --drive-id, --item-id, --new-name (or --from-plan)",
              file=sys.stderr)
        return 2

    graph = build_graph_client(cfg, scope)
    meta = _lookup_item(graph, drive_id, item_id)
    try:
        assert_scope_allowed(type("X", (), meta)(), cfg, unsafe_scope=unsafe_scope)
    except ScopeViolation as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    op = Operation(
        op_id=new_op_id(), action="rename",
        drive_id=drive_id, item_id=item_id,
        args={"new_name": new_name},
        dry_run_result=f"would rename {meta['full_path']} -> "
                       f"{meta['parent_path']}/{new_name}",
    )
    plan = new_plan(
        source_cmd=f"od-rename --drive-id {drive_id} --item-id {item_id} "
                   f"--new-name {new_name!r}",
        scope=scope or "",
        operations=[op],
    )

    if confirm:
        result = execute_rename(op, graph, logger,
                                before={"parent_path": meta["parent_path"],
                                        "name": meta["name"]})
        if result.status != "ok":
            print(f"error: {result.error}", file=sys.stderr)
            return 1
        print(f"[{op.op_id}] ok")
        return 0

    emit_plan(plan, plan_out=plan_out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-rename")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--scope")
    p.add_argument("--drive-id")
    p.add_argument("--item-id")
    p.add_argument("--new-name")
    p.add_argument("--from-plan", type=Path)
    p.add_argument("--plan-out", type=Path)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--unsafe-scope", action="store_true")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_rename(
        config_path=Path(args.config),
        scope=args.scope, drive_id=args.drive_id,
        item_id=args.item_id, new_name=args.new_name,
        from_plan=args.from_plan, plan_out=args.plan_out,
        confirm=args.confirm, unsafe_scope=args.unsafe_scope,
    )
```

- [ ] **Step 8: Wire into the dispatcher**

Edit `src/m365ctl/cli/__main__.py` — extend `_SUBCOMMANDS`:
```python
from m365ctl.cli import auth as auth_cli
from m365ctl.cli import catalog as catalog_cli
from m365ctl.cli import inventory as inventory_cli
from m365ctl.cli import move as move_cli
from m365ctl.cli import rename as rename_cli

_SUBCOMMANDS = {
    "auth": auth_cli.main,
    "catalog": catalog_cli.main,
    "inventory": inventory_cli.main,
    "move": move_cli.main,
    "rename": rename_cli.main,
}
```

- [ ] **Step 9: Run tests**

```bash
uv run pytest tests/test_mutate_move.py tests/test_mutate_rename.py \
              tests/test_cli_move.py tests/test_cli_rename.py -v
```
Expected: 7 passed (2 move mutate + 1 rename mutate + 3 cli move + 1 cli rename).

- [ ] **Step 10: Commit**

```bash
git add src/m365ctl/mutate/ src/m365ctl/cli/move.py src/m365ctl/cli/rename.py \
        src/m365ctl/cli/_common.py src/m365ctl/cli/__main__.py \
        tests/test_mutate_move.py tests/test_mutate_rename.py \
        tests/test_cli_move.py tests/test_cli_rename.py
git commit -m "feat(mutate): od-move and od-rename with plan-file + audit + safety"
```

---

### Task 6: `od-copy` (async via `Location` polling)

**Files:**
- Create: `src/m365ctl/mutate/copy.py`
- Create: `src/m365ctl/cli/copy.py`
- Create: `tests/test_mutate_copy.py`
- Create: `tests/test_cli_copy.py`
- Modify: `src/m365ctl/cli/__main__.py`

- [ ] **Step 1: Write failing tests for `mutate/copy.py`**

Create `tests/test_mutate_copy.py`:
```python
from __future__ import annotations

import httpx

from m365ctl.audit import AuditLogger, iter_audit_entries
from m365ctl.graph import GraphClient
from m365ctl.mutate.copy import execute_copy
from m365ctl.planfile import Operation


def test_execute_copy_polls_location_until_complete(tmp_path):
    seq = iter([
        # POST /copy -> 202 with Location
        httpx.Response(202, headers={"Location": "https://graph/monitor/job1"},
                       json={}),
        # GET monitor -> 200 inProgress
        httpx.Response(200, json={"status": "inProgress", "percentageComplete": 50}),
        # GET monitor -> 200 completed with resourceId
        httpx.Response(200, json={"status": "completed",
                                  "resourceId": "NEW-ITEM-ID",
                                  "resourceLocation":
                                      "https://graph.microsoft.com/v1.0/drives/d2/items/NEW-ITEM-ID"}),
    ])

    def handler(request):
        return next(seq)

    client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-1", action="copy", drive_id="d1", item_id="i1",
                   args={"target_drive_id": "d2", "target_parent_item_id": "P",
                         "new_name": "copy.txt"},
                   dry_run_result="")
    result = execute_copy(op, client, logger,
                          before={"parent_path": "/A", "name": "x.txt"},
                          poll_interval=0.0, max_wait_seconds=5)

    assert result.status == "ok"
    assert result.after["new_item_id"] == "NEW-ITEM-ID"
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-1"]
    assert entries[-1]["after"]["new_item_id"] == "NEW-ITEM-ID"


def test_execute_copy_times_out(tmp_path):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(202,
                                  headers={"Location": "https://graph/monitor/j"},
                                  json={})
        return httpx.Response(200, json={"status": "inProgress",
                                         "percentageComplete": 10})

    client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-2", action="copy", drive_id="d1", item_id="i1",
                   args={"target_drive_id": "d2", "target_parent_item_id": "P",
                         "new_name": "y.txt"},
                   dry_run_result="")
    result = execute_copy(op, client, logger,
                          before={"parent_path": "/", "name": "x"},
                          poll_interval=0.0, max_wait_seconds=0.0)
    assert result.status == "error"
    assert "timeout" in result.error.lower()
```

- [ ] **Step 2: Implement `mutate/copy.py`**

Create `src/m365ctl/mutate/copy.py`:
```python
"""OneDrive COPY via Graph POST .../items/{id}/copy (async).

Graph responds 202 with a ``Location`` header pointing at a monitor URL.
Poll the monitor until status == 'completed' (or 'failed'). On success
the response includes ``resourceId`` (the new item's id) — we surface it
in the audit ``after`` block so undo can find the copy to delete it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from m365ctl.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.graph import GraphClient, GraphError
from m365ctl.planfile import Operation


@dataclass(frozen=True)
class CopyResult:
    op_id: str
    status: str
    error: str | None = None
    after: dict[str, Any] | None = None


def execute_copy(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
    poll_interval: float = 2.0,
    max_wait_seconds: float = 300.0,
) -> CopyResult:
    target_drive = op.args["target_drive_id"]
    target_parent = op.args["target_parent_item_id"]
    new_name = op.args.get("new_name", before.get("name", ""))

    log_mutation_start(
        logger, op_id=op.op_id, cmd="od-copy",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id,
        before=before,
    )

    try:
        resp = graph.post_raw(
            f"/drives/{op.drive_id}/items/{op.item_id}/copy",
            json_body={
                "parentReference": {"driveId": target_drive, "id": target_parent},
                "name": new_name,
            },
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return CopyResult(op_id=op.op_id, status="error", error=str(e))

    monitor_url = resp.headers.get("Location")
    if resp.status_code == 200 and not monitor_url:
        # Some copies complete synchronously.
        body = resp.json() if resp.content else {}
        after = {"new_item_id": body.get("id", ""), "new_name": new_name,
                 "target_drive_id": target_drive,
                 "target_parent_item_id": target_parent}
        log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
        return CopyResult(op_id=op.op_id, status="ok", after=after)

    if not monitor_url:
        err = f"copy POST returned {resp.status_code} with no Location header"
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=err)
        return CopyResult(op_id=op.op_id, status="error", error=err)

    waited = 0.0
    while True:
        try:
            status_body = graph.get_absolute(monitor_url)
        except GraphError as e:
            log_mutation_end(logger, op_id=op.op_id, after=None,
                             result="error", error=str(e))
            return CopyResult(op_id=op.op_id, status="error", error=str(e))
        status = status_body.get("status")
        if status == "completed":
            after = {
                "new_item_id": status_body.get("resourceId", ""),
                "new_name": new_name,
                "target_drive_id": target_drive,
                "target_parent_item_id": target_parent,
            }
            log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
            return CopyResult(op_id=op.op_id, status="ok", after=after)
        if status == "failed":
            err = f"copy job failed: {status_body!r}"
            log_mutation_end(logger, op_id=op.op_id, after=None,
                             result="error", error=err)
            return CopyResult(op_id=op.op_id, status="error", error=err)

        if waited >= max_wait_seconds:
            err = f"copy timeout after {waited}s (last status {status!r})"
            log_mutation_end(logger, op_id=op.op_id, after=None,
                             result="error", error=err)
            return CopyResult(op_id=op.op_id, status="error", error=err)
        time.sleep(poll_interval) if False else graph._sleep(poll_interval)
        waited += poll_interval
```

(Note: `graph._sleep` re-uses the injectable sleep already on GraphClient — keeps the test's `sleep=lambda s: None` path working without plumbing a second sleep argument.)

- [ ] **Step 3: CLI for `od-copy`**

Create `tests/test_cli_copy.py` mirroring `test_cli_move.py` (single-item dry-run; `--pattern --confirm` without `--from-plan` rejected; `--from-plan` executes one copy per op with counting transport). Create `src/m365ctl/cli/copy.py` structured like `cli/move.py`: selection (`--item-id/--drive-id` or `--pattern` or `--from-plan`) → safety filter → plan → emit or execute.

- [ ] **Step 4: Register in dispatcher**

Add `"copy": copy_cli.main` to `_SUBCOMMANDS`.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_mutate_copy.py tests/test_cli_copy.py -v
```
Expected: 5 passed (2 mutate + 3 cli).

- [ ] **Step 6: Commit**

```bash
git add src/m365ctl/mutate/copy.py src/m365ctl/cli/copy.py \
        src/m365ctl/cli/__main__.py \
        tests/test_mutate_copy.py tests/test_cli_copy.py
git commit -m "feat(mutate): od-copy with async Location polling"
```

---

### Task 7: `od-delete` (recycle-bin) + basic undo restore path

**Files:**
- Create: `src/m365ctl/mutate/delete.py`
- Create: `src/m365ctl/cli/delete.py`
- Create: `tests/test_mutate_delete.py`
- Create: `tests/test_cli_delete.py`
- Modify: `src/m365ctl/cli/__main__.py`

- [ ] **Step 1: Tests for `mutate/delete.py`**

Create `tests/test_mutate_delete.py`:
```python
from __future__ import annotations

import httpx

from m365ctl.audit import AuditLogger, iter_audit_entries
from m365ctl.graph import GraphClient
from m365ctl.mutate.delete import execute_recycle_delete, execute_restore
from m365ctl.planfile import Operation


def _client(handler):
    return GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )


def test_delete_routes_to_recycle_not_permadelete(tmp_path):
    seen: list[tuple[str, str]] = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(204)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-1", action="delete", drive_id="d1", item_id="i1",
                   args={}, dry_run_result="")
    result = execute_recycle_delete(op, _client(handler), logger,
                                    before={"parent_path": "/", "name": "x.txt"})
    assert result.status == "ok"
    # Spec §7 rule 6: no /permanentDelete path.
    assert seen == [("DELETE", "/drives/d1/items/i1")]


def test_restore_calls_restore_endpoint(tmp_path):
    seen: list[tuple[str, str]] = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={"id": "i1", "name": "x.txt",
                  "parentReference": {"id": "P", "path": "/drive/root:/A"}},
        )

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-2", action="restore", drive_id="d1", item_id="i1",
                   args={}, dry_run_result="")
    result = execute_restore(op, _client(handler), logger,
                             before={"parent_path": "(recycle bin)", "name": "x.txt"})
    assert result.status == "ok"
    assert seen == [("POST", "/drives/d1/items/i1/restore")]
```

- [ ] **Step 2: Implement `mutate/delete.py`**

Create `src/m365ctl/mutate/delete.py`:
```python
"""OneDrive DELETE (recycle) + RESTORE (from recycle).

Spec §7 rule 6: no hard deletes here. The Graph ``DELETE
/drives/{d}/items/{i}`` endpoint on OneDrive is a SOFT delete — the item
goes to the recycle bin. Hard-delete lives in ``mutate/clean.py``
(``od-clean recycle-bin``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from m365ctl.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.graph import GraphClient, GraphError
from m365ctl.planfile import Operation


@dataclass(frozen=True)
class DeleteResult:
    op_id: str
    status: str
    error: str | None = None
    after: dict[str, Any] | None = None


def execute_recycle_delete(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> DeleteResult:
    log_mutation_start(
        logger, op_id=op.op_id, cmd="od-delete",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id,
        before=before,
    )
    try:
        graph.delete(f"/drives/{op.drive_id}/items/{op.item_id}")
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return DeleteResult(op_id=op.op_id, status="error", error=str(e))
    after = {"parent_path": "(recycle bin)", "name": before.get("name", ""),
             "recycled_from": before.get("parent_path", "")}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return DeleteResult(op_id=op.op_id, status="ok", after=after)


def execute_restore(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> DeleteResult:
    log_mutation_start(
        logger, op_id=op.op_id, cmd="od-undo(restore)",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id,
        before=before,
    )
    try:
        body = {"parentReference": {"id": op.args["parent_item_id"]}} \
            if "parent_item_id" in op.args else None
        resp = graph.post_raw(
            f"/drives/{op.drive_id}/items/{op.item_id}/restore",
            json_body=body,
        )
        data = resp.json() if resp.content else {}
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return DeleteResult(op_id=op.op_id, status="error", error=str(e))
    after = {
        "parent_path": (data.get("parentReference") or {}).get("path", ""),
        "name": data.get("name", before.get("name", "")),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return DeleteResult(op_id=op.op_id, status="ok", after=after)
```

- [ ] **Step 3: CLI for `od-delete`**

Create `src/m365ctl/cli/delete.py` following the `od-move` pattern. Support single-item `--item-id --drive-id --confirm` and bulk `--pattern --plan-out` / `--from-plan --confirm`. Every delete calls `assert_scope_allowed` before firing; every delete logs through audit.

Create `tests/test_cli_delete.py` with:
- `test_dry_run_is_default_no_graph_call`
- `test_confirm_required_to_delete`
- `test_pattern_with_confirm_requires_from_plan`
- `test_deny_paths_filtered_from_plan`

- [ ] **Step 4: Register + run tests**

```bash
uv run pytest tests/test_mutate_delete.py tests/test_cli_delete.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/mutate/delete.py src/m365ctl/cli/delete.py \
        src/m365ctl/cli/__main__.py \
        tests/test_mutate_delete.py tests/test_cli_delete.py
git commit -m "feat(mutate): od-delete routes to recycle bin; restore helper"
```

---

### Task 8: `od-clean` (recycle purge, old versions, stale shares)

**Files:**
- Create: `src/m365ctl/mutate/clean.py`
- Create: `src/m365ctl/cli/clean.py`
- Create: `tests/test_mutate_clean.py`
- Create: `tests/test_cli_clean.py`
- Modify: `src/m365ctl/cli/__main__.py`

- [ ] **Step 1: Tests for `mutate/clean.py`**

Create `tests/test_mutate_clean.py`:
```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from m365ctl.audit import AuditLogger
from m365ctl.graph import GraphClient
from m365ctl.mutate.clean import (
    purge_recycle_bin_item,
    remove_old_versions,
    revoke_stale_shares,
)
from m365ctl.planfile import Operation


def _client(handler):
    return GraphClient(token_provider=lambda: "t",
                       transport=httpx.MockTransport(handler),
                       sleep=lambda s: None)


def test_recycle_bin_purge_is_explicit_command(tmp_path):
    """Only od-clean calls permanentDelete; od-delete never does."""
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(204)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-1", action="recycle-purge",
                   drive_id="d1", item_id="I",
                   args={}, dry_run_result="")
    result = purge_recycle_bin_item(op, _client(handler), logger,
                                    before={"parent_path": "(recycle bin)",
                                            "name": "old.txt"})
    assert result.status == "ok"
    assert any("permanentDelete" in p for _, p in calls)


def test_remove_old_versions_keeps_n_most_recent(tmp_path):
    now = datetime.now(timezone.utc)
    versions = [
        {"id": f"v{i}", "lastModifiedDateTime":
         (now - timedelta(days=i)).isoformat().replace("+00:00", "Z")}
        for i in range(5)  # v0 newest, v4 oldest
    ]
    deleted: list[str] = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"value": versions})
        if request.method == "DELETE":
            deleted.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(204)
        return httpx.Response(405)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-2", action="version-delete",
                   drive_id="d1", item_id="i1",
                   args={"keep": 2}, dry_run_result="")
    result = remove_old_versions(op, _client(handler), logger,
                                 before={"parent_path": "/", "name": "x"})
    assert result.status == "ok"
    # With keep=2, delete v2, v3, v4 (3 deletions).
    assert set(deleted) == {"v2", "v3", "v4"}


def test_revoke_stale_shares_only_touches_links_older_than_cutoff(tmp_path):
    now = datetime.now(timezone.utc)
    perms = [
        {"id": "p-fresh",
         "link": {"createdDateTime":
                  (now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                  "scope": "anonymous", "type": "view"}},
        {"id": "p-stale",
         "link": {"createdDateTime":
                  (now - timedelta(days=400)).isoformat().replace("+00:00", "Z"),
                  "scope": "anonymous", "type": "view"}},
        # owner permission, no link block — must be skipped
        {"id": "p-owner", "roles": ["owner"]},
    ]
    deleted: list[str] = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"value": perms})
        if request.method == "DELETE":
            deleted.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(204)
        return httpx.Response(405)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-3", action="share-revoke",
                   drive_id="d1", item_id="i1",
                   args={"older_than_days": 90}, dry_run_result="")
    result = revoke_stale_shares(op, _client(handler), logger,
                                 before={"parent_path": "/", "name": "x"})
    assert result.status == "ok"
    assert deleted == ["p-stale"]
```

- [ ] **Step 2: Implement `mutate/clean.py`**

Create `src/m365ctl/mutate/clean.py`:
```python
"""Specialised cleanup ops: recycle-bin purge, old-versions, stale-shares.

Each function takes an Operation + GraphClient + AuditLogger and returns
a DeleteResult-shaped record. All three go through the standard
start/end audit lifecycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from m365ctl.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.graph import GraphClient, GraphError
from m365ctl.planfile import Operation


@dataclass(frozen=True)
class CleanResult:
    op_id: str
    status: str
    error: str | None = None
    after: dict[str, Any] | None = None


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def purge_recycle_bin_item(
    op: Operation, graph: GraphClient, logger: AuditLogger,
    *, before: dict[str, Any],
) -> CleanResult:
    """HARD delete a recycle-bin item. Not reversible."""
    log_mutation_start(logger, op_id=op.op_id, cmd="od-clean(recycle-bin)",
                       args=op.args, drive_id=op.drive_id,
                       item_id=op.item_id, before=before)
    try:
        graph.post_raw(
            f"/drives/{op.drive_id}/items/{op.item_id}/permanentDelete",
            json_body=None,
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return CleanResult(op_id=op.op_id, status="error", error=str(e))
    after = {"parent_path": "(permanently deleted)",
             "name": before.get("name", ""),
             "irreversible": True}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return CleanResult(op_id=op.op_id, status="ok", after=after)


def remove_old_versions(
    op: Operation, graph: GraphClient, logger: AuditLogger,
    *, before: dict[str, Any],
) -> CleanResult:
    """Keep ``args['keep']`` most-recent versions; delete the rest."""
    keep = int(op.args.get("keep", 3))
    log_mutation_start(logger, op_id=op.op_id, cmd="od-clean(old-versions)",
                       args=op.args, drive_id=op.drive_id,
                       item_id=op.item_id, before=before)
    try:
        body = graph.get(f"/drives/{op.drive_id}/items/{op.item_id}/versions")
        versions = sorted(
            body.get("value", []),
            key=lambda v: _parse_ts(v["lastModifiedDateTime"]),
            reverse=True,
        )
        doomed = versions[keep:]
        deleted_ids: list[str] = []
        for v in doomed:
            graph.delete(
                f"/drives/{op.drive_id}/items/{op.item_id}/versions/{v['id']}"
            )
            deleted_ids.append(v["id"])
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return CleanResult(op_id=op.op_id, status="error", error=str(e))
    after = {"parent_path": before.get("parent_path", ""),
             "name": before.get("name", ""),
             "versions_deleted": deleted_ids,
             "versions_kept": [v["id"] for v in versions[:keep]]}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return CleanResult(op_id=op.op_id, status="ok", after=after)


def revoke_stale_shares(
    op: Operation, graph: GraphClient, logger: AuditLogger,
    *, before: dict[str, Any],
) -> CleanResult:
    """Revoke sharing links older than ``args['older_than_days']``."""
    cutoff_days = int(op.args.get("older_than_days", 90))
    cutoff = datetime.now(timezone.utc) - timedelta(days=cutoff_days)
    log_mutation_start(logger, op_id=op.op_id, cmd="od-clean(stale-shares)",
                       args=op.args, drive_id=op.drive_id,
                       item_id=op.item_id, before=before)
    try:
        body = graph.get(f"/drives/{op.drive_id}/items/{op.item_id}/permissions")
        stale: list[str] = []
        for perm in body.get("value", []):
            link = perm.get("link")
            if not link:
                continue  # owner/direct permissions stay
            created = link.get("createdDateTime")
            if not created:
                continue
            if _parse_ts(created) < cutoff:
                graph.delete(
                    f"/drives/{op.drive_id}/items/{op.item_id}"
                    f"/permissions/{perm['id']}"
                )
                stale.append(perm["id"])
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=str(e))
        return CleanResult(op_id=op.op_id, status="error", error=str(e))
    after = {"parent_path": before.get("parent_path", ""),
             "name": before.get("name", ""),
             "permissions_revoked": stale}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return CleanResult(op_id=op.op_id, status="ok", after=after)
```

- [ ] **Step 3: `cli/clean.py`**

Create `src/m365ctl/cli/clean.py` with three subcommands: `recycle-bin`, `old-versions`, `stale-shares`, each producing its own plan (catalog-driven where applicable, via `expand_pattern`) and respecting `--scope` / `--confirm` / `--from-plan` exactly like `od-move`.

Create `tests/test_cli_clean.py` with:
- `test_recycle_bin_dry_run_emits_plan_of_recycled_items`
- `test_old_versions_plan_one_op_per_item`
- `test_stale_shares_older_than_days_honored`
- `test_confirm_required_to_execute`

- [ ] **Step 4: Register + run tests**

```bash
uv run pytest tests/test_mutate_clean.py tests/test_cli_clean.py -v
```
Expected: 7 passed (3 mutate + 4 cli).

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/mutate/clean.py src/m365ctl/cli/clean.py \
        src/m365ctl/cli/__main__.py \
        tests/test_mutate_clean.py tests/test_cli_clean.py
git commit -m "feat(mutate): od-clean subcommands (recycle-bin, old-versions, stale-shares)"
```

---

### Task 9: THE safety test suite — adversarial coverage of spec §7

**Files:**
- Modify: `tests/test_safety.py` — append the full adversarial suite

- [ ] **Step 1: Append tests**

Append to `tests/test_safety.py`:
```python
# ---------------------------------------------------------------- §7 invariants
# Each test below cross-references the rule it covers; see the invariant
# table at the top of the Plan 4 document.

import json
from unittest.mock import MagicMock, patch

import httpx

from m365ctl.cli.move import run_move


def test_dry_run_is_default_no_mutation(tmp_path, mocker):
    """Spec §7 rule 1: mutating command without --confirm issues zero Graph calls."""
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    mocker.patch("m365ctl.cli.move.load_config", return_value=cfg)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={})

    from m365ctl.graph import GraphClient
    client = GraphClient(token_provider=lambda: "t",
                         transport=httpx.MockTransport(handler),
                         sleep=lambda s: None)
    mocker.patch("m365ctl.cli.move.build_graph_client", return_value=client)
    mocker.patch(
        "m365ctl.cli.move._lookup_item",
        return_value={"drive_id": "d1", "item_id": "i1",
                      "full_path": "/x", "name": "x", "parent_path": "/"},
    )

    rc = run_move(
        config_path=tmp_path / "c.toml",
        scope="drive:d1", drive_id="d1", item_id="i1",
        pattern=None, from_plan=None,
        new_parent_path="/B", new_parent_item_id="PB",
        plan_out=None, confirm=False, unsafe_scope=False,
    )
    assert rc == 0
    # _lookup_item does its own GET; but no PATCH/POST/DELETE must fire.
    # Assert by method, not total.
    # (Since our handler returned 200 json={}, count is the GET for lookup.)
    assert calls["n"] <= 1  # at most one GET for item metadata, zero mutations


def test_pattern_plus_confirm_is_rejected(tmp_path, mocker, capsys):
    """Spec §7 rule 2: bulk destructive requires plan file."""
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    mocker.patch("m365ctl.cli.move.load_config", return_value=cfg)
    rc = run_move(
        config_path=tmp_path / "c.toml",
        scope="drive:d1", drive_id=None, item_id=None,
        pattern="**/*.tmp", from_plan=None,
        new_parent_path="/T", new_parent_item_id="T",
        plan_out=None, confirm=True, unsafe_scope=False,
    )
    assert rc == 2
    assert "plan" in capsys.readouterr().err.lower()


def test_from_plan_no_glob_reexpansion_exact_call_count(tmp_path, mocker):
    """Spec §7 rule 2: --from-plan does NOT re-expand globs.

    Plan has 2 op_ids. Even if the catalog still contains 100 matches for the
    original pattern, exactly 2 Graph PATCHes must fire.
    """
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    mocker.patch("m365ctl.cli.move.load_config", return_value=cfg)

    # Seed catalog with 100 items to prove non-expansion.
    from m365ctl.catalog.db import open_catalog
    with open_catalog(cfg.catalog.path) as conn:
        for i in range(100):
            conn.execute(
                "INSERT INTO items (drive_id, item_id, name, full_path, "
                "parent_path, is_folder, is_deleted) VALUES "
                "(?, ?, ?, ?, ?, false, false)",
                ["d1", f"i{i}", f"x{i}.tmp", f"/junk/x{i}.tmp", "/junk"],
            )

    patches = {"n": 0}

    def handler(request):
        if request.method == "PATCH":
            patches["n"] += 1
        return httpx.Response(
            200, json={"id": "x",
                       "parentReference": {"id": "P", "path": "/B"},
                       "name": "x"},
        )

    from m365ctl.graph import GraphClient
    client = GraphClient(token_provider=lambda: "t",
                         transport=httpx.MockTransport(handler),
                         sleep=lambda s: None)
    mocker.patch("m365ctl.cli.move.build_graph_client", return_value=client)
    mocker.patch(
        "m365ctl.cli.move._lookup_item",
        side_effect=lambda g, d, i: {"drive_id": d, "item_id": i,
                                     "full_path": f"/junk/{i}", "name": i,
                                     "parent_path": "/junk"},
    )

    from m365ctl.planfile import PLAN_SCHEMA_VERSION
    plan_payload = {
        "version": PLAN_SCHEMA_VERSION,
        "created_at": "2026-04-24T10:00:00+00:00",
        "source_cmd": "od-move --pattern '/junk/**' ...",
        "scope": "drive:d1",
        "operations": [
            {"op_id": f"op-{i}", "action": "move",
             "drive_id": "d1", "item_id": f"i{i}",
             "args": {"new_parent_item_id": "PB"},
             "dry_run_result": ""} for i in range(2)
        ],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan_payload))

    rc = run_move(
        config_path=tmp_path / "c.toml",
        scope=None, drive_id=None, item_id=None, pattern=None,
        from_plan=plan_path,
        new_parent_path=None, new_parent_item_id=None,
        plan_out=None, confirm=True, unsafe_scope=False,
    )
    assert rc == 0
    assert patches["n"] == 2  # NOT 100


def test_piped_stdin_cannot_auto_confirm_unsafe_scope(tmp_path, monkeypatch):
    """Spec §7 rule 3: /dev/tty, not stdin, drives the unsafe-scope confirm.

    Piping 'y\\n' to stdin must NOT pre-answer the prompt. We simulate
    /dev/tty absent (OSError) which makes _confirm_via_tty return False.
    """
    from m365ctl.safety import ScopeViolation, assert_scope_allowed

    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    item = _Item(drive_id="OTHER", item_id="i", full_path="/foo")

    # Redirect stdin to a 'y'-stream as the adversary would.
    monkeypatch.setattr("sys.stdin", io.StringIO("y\ny\ny\n"))
    # And make /dev/tty open fail (simulating a headless / piped context).
    real_open = open

    def fake_open(path, *a, **kw):
        if path == "/dev/tty":
            raise OSError("no controlling tty")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", fake_open)

    with pytest.raises(ScopeViolation, match="declined"):
        assert_scope_allowed(item, cfg, unsafe_scope=True)


def test_deny_paths_never_appear_in_plan_or_tsv(tmp_path, mocker, capsys):
    """Spec §7 rule 4: deny-paths filtered BEFORE plan emission."""
    cfg = _cfg(allow=["d1"], deny=["/Confidential/**"], tmp_path=tmp_path)
    mocker.patch("m365ctl.cli.move.load_config", return_value=cfg)

    from m365ctl.catalog.db import open_catalog
    with open_catalog(cfg.catalog.path) as conn:
        conn.execute(
            "INSERT INTO items (drive_id, item_id, name, full_path, "
            "parent_path, is_folder, is_deleted) VALUES "
            "('d1','ok','pub.txt','/Public/pub.txt','/Public',false,false),"
            "('d1','no','sec.docx','/Confidential/sec.docx','/Confidential',false,false)"
        )

    plan_path = tmp_path / "plan.json"
    rc = run_move(
        config_path=tmp_path / "c.toml",
        scope="drive:d1", drive_id=None, item_id=None,
        pattern="/*/*",
        from_plan=None,
        new_parent_path="/Elsewhere", new_parent_item_id="X",
        plan_out=plan_path, confirm=False, unsafe_scope=False,
    )
    assert rc == 0
    plan = json.loads(plan_path.read_text())
    names = [op["item_id"] for op in plan["operations"]]
    assert "ok" in names
    assert "no" not in names  # deny-path item filtered


def test_audit_start_line_persists_even_on_mid_mutation_crash(tmp_path):
    """Spec §7 rule 5: audit 'start' is written BEFORE the Graph call.

    Simulate a connection failure after the start record hits disk.
    """
    from m365ctl.audit import AuditLogger, iter_audit_entries
    from m365ctl.mutate.move import execute_move
    from m365ctl.planfile import Operation

    def handler(request):
        # Simulate TCP reset mid-mutation.
        raise httpx.ConnectError("connection reset by peer")

    from m365ctl.graph import GraphClient
    client = GraphClient(token_provider=lambda: "t",
                         transport=httpx.MockTransport(handler),
                         sleep=lambda s: None)
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="CRASH", action="move", drive_id="d1", item_id="i1",
                   args={"new_parent_item_id": "P"}, dry_run_result="")

    with pytest.raises(httpx.ConnectError):
        execute_move(op, client, logger,
                     before={"parent_path": "/A", "name": "x"})

    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "CRASH"]
    # The 'start' record is on disk even though the 'end' never ran.
    assert len(entries) >= 1
    assert entries[0]["phase"] == "start"
```

- [ ] **Step 2: Run the full safety suite**

```bash
uv run pytest tests/test_safety.py -v
```
Expected: 14 passed (8 from Task 3 + 6 new adversarial).

- [ ] **Step 3: Commit**

```bash
git add tests/test_safety.py
git commit -m "test(safety): adversarial suite covering spec §7 invariants 1-6"
```

---

### Task 10: `od-label` via PnP.PowerShell

**Files:**
- Create: `src/m365ctl/mutate/label.py`
- Create: `src/m365ctl/cli/label.py`
- Create: `scripts/ps/Set-m365ctlLabel.ps1`
- Create: `tests/test_mutate_label.py`
- Create: `tests/test_cli_label.py`
- Modify: `src/m365ctl/cli/__main__.py`

- [ ] **Step 1: Tests for label module (subprocess mocked)**

Create `tests/test_mutate_label.py`:
```python
from __future__ import annotations

import json
from unittest.mock import MagicMock

from m365ctl.audit import AuditLogger, iter_audit_entries
from m365ctl.mutate.label import execute_label_apply, execute_label_remove
from m365ctl.planfile import Operation


def test_apply_label_invokes_pwsh_and_logs(tmp_path, mocker):
    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = json.dumps({"status": "ok", "label": "Confidential"})
    completed.stderr = ""
    run = mocker.patch("m365ctl.mutate.label.subprocess.run",
                       return_value=completed)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-1", action="label-apply", drive_id="d1",
                   item_id="i1", args={"label": "Confidential",
                                        "site_url": "https://example.sharepoint.com/"},
                   dry_run_result="")
    result = execute_label_apply(op, logger,
                                 before={"parent_path": "/", "name": "x",
                                         "server_relative_url": "/Documents/x"})
    assert result.status == "ok"
    run.assert_called_once()
    # pwsh invoked with the shared ps1 and args.
    cmd = run.call_args[0][0]
    assert cmd[0] == "pwsh"
    assert any("Set-m365ctlLabel.ps1" in a for a in cmd)
    assert "Confidential" in cmd
    entries = [e for e in iter_audit_entries(logger) if e["op_id"] == "op-1"]
    assert entries[-1]["result"] == "ok"


def test_remove_label_invokes_pwsh_and_logs_error_on_nonzero(tmp_path, mocker):
    completed = MagicMock()
    completed.returncode = 1
    completed.stdout = ""
    completed.stderr = "Set-PnPFileSensitivityLabel : access denied"
    mocker.patch("m365ctl.mutate.label.subprocess.run", return_value=completed)

    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    op = Operation(op_id="op-2", action="label-remove", drive_id="d1",
                   item_id="i1", args={"site_url":
                                       "https://example.sharepoint.com/"},
                   dry_run_result="")
    result = execute_label_remove(op, logger,
                                  before={"parent_path": "/", "name": "x",
                                          "server_relative_url":
                                              "/Documents/x"})
    assert result.status == "error"
    assert "access denied" in result.error.lower()
```

- [ ] **Step 2: Implement `mutate/label.py`**

Create `src/m365ctl/mutate/label.py`:
```python
"""Sensitivity-label operations via PnP.PowerShell.

Graph does not expose label apply/remove for SharePoint sensitivity
labels in v1.0 in a way Python can drive reliably. We shell out to
``pwsh`` + ``Set-PnPFileSensitivityLabel`` (reused setup from Plan 3).
The PowerShell stub takes JSON on stdin describing the op and emits JSON
on stdout describing the result.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from m365ctl.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.planfile import Operation

_PS1 = Path(__file__).resolve().parents[2].parent / "scripts" / "ps" / "Set-m365ctlLabel.ps1"


@dataclass(frozen=True)
class LabelResult:
    op_id: str
    status: str
    error: str | None = None
    after: dict[str, Any] | None = None


def _invoke(ps_args: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(_PS1), *ps_args],
        capture_output=True, text=True, check=False,
    )
    return result.returncode, result.stdout, result.stderr


def execute_label_apply(
    op: Operation, logger: AuditLogger, *, before: dict[str, Any],
) -> LabelResult:
    log_mutation_start(logger, op_id=op.op_id, cmd="od-label(apply)",
                       args=op.args, drive_id=op.drive_id,
                       item_id=op.item_id, before=before)
    code, out, err = _invoke([
        "-Action", "apply",
        "-SiteUrl", op.args["site_url"],
        "-ServerRelativeUrl", before["server_relative_url"],
        "-Label", op.args["label"],
    ])
    if code != 0:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=err.strip() or out.strip())
        return LabelResult(op_id=op.op_id, status="error",
                           error=err.strip() or out.strip())
    after = {"parent_path": before.get("parent_path", ""),
             "name": before.get("name", ""),
             "label": op.args["label"]}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return LabelResult(op_id=op.op_id, status="ok", after=after)


def execute_label_remove(
    op: Operation, logger: AuditLogger, *, before: dict[str, Any],
) -> LabelResult:
    log_mutation_start(logger, op_id=op.op_id, cmd="od-label(remove)",
                       args=op.args, drive_id=op.drive_id,
                       item_id=op.item_id, before=before)
    code, out, err = _invoke([
        "-Action", "remove",
        "-SiteUrl", op.args["site_url"],
        "-ServerRelativeUrl", before["server_relative_url"],
    ])
    if code != 0:
        log_mutation_end(logger, op_id=op.op_id, after=None,
                         result="error", error=err.strip() or out.strip())
        return LabelResult(op_id=op.op_id, status="error",
                           error=err.strip() or out.strip())
    after = {"parent_path": before.get("parent_path", ""),
             "name": before.get("name", ""),
             "label": None}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return LabelResult(op_id=op.op_id, status="ok", after=after)
```

- [ ] **Step 3: Write the PowerShell stub**

Create `scripts/ps/Set-m365ctlLabel.ps1`:
```powershell
<#
.SYNOPSIS
Apply or remove a sensitivity label on a SharePoint file via PnP.PowerShell.

.PARAMETER Action
'apply' or 'remove'.

.PARAMETER SiteUrl
Full site URL, e.g. 'https://example.sharepoint.com/sites/Finance'.

.PARAMETER ServerRelativeUrl
Server-relative file path, e.g. '/sites/Finance/Shared Documents/Q1.xlsx'.

.PARAMETER Label
Label display name (required for apply).

.NOTES
Plan 3 installs PnP.PowerShell and converts the cert to PFX. This script
relies on both being already in place. It authenticates with certificate
+ app-only against the m365ctl tenant using env vars set by the caller
(FAZLA_OD_TENANT, FAZLA_OD_CLIENT_ID, FAZLA_OD_CERT_PFX).
#>
param(
    [Parameter(Mandatory=$true)][ValidateSet('apply','remove')][string]$Action,
    [Parameter(Mandatory=$true)][string]$SiteUrl,
    [Parameter(Mandatory=$true)][string]$ServerRelativeUrl,
    [string]$Label
)

$ErrorActionPreference = 'Stop'

Import-Module PnP.PowerShell -ErrorAction Stop
Connect-PnPOnline `
    -Url $SiteUrl `
    -Tenant $env:FAZLA_OD_TENANT `
    -ClientId $env:FAZLA_OD_CLIENT_ID `
    -CertificatePath $env:FAZLA_OD_CERT_PFX `
    -CertificatePassword (ConvertTo-SecureString $env:FAZLA_OD_CERT_PFX_PASS -AsPlainText -Force)

try {
    if ($Action -eq 'apply') {
        if (-not $Label) { throw "Label required for 'apply'." }
        Set-PnPFileSensitivityLabel -ServerRelativeUrl $ServerRelativeUrl -Label $Label | Out-Null
        $payload = @{ status = 'ok'; label = $Label; path = $ServerRelativeUrl }
    } else {
        Remove-PnPFileSensitivityLabel -ServerRelativeUrl $ServerRelativeUrl | Out-Null
        $payload = @{ status = 'ok'; label = $null; path = $ServerRelativeUrl }
    }
    $payload | ConvertTo-Json -Compress
    exit 0
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
finally {
    Disconnect-PnPOnline
}
```

- [ ] **Step 4: `cli/label.py`**

Create `src/m365ctl/cli/label.py` with two subcommands — `apply --label <name>` and `remove` — each taking `--item-id/--drive-id` or `--from-plan`. Must call `assert_scope_allowed` before shelling out. Must produce plan JSON with `action="label-apply"` / `action="label-remove"` when `--plan-out`.

Create `tests/test_cli_label.py` covering dry-run default, `--confirm` required, `--from-plan` single pwsh invocation per op (subprocess mocked).

- [ ] **Step 5: Register + run tests**

```bash
uv run pytest tests/test_mutate_label.py tests/test_cli_label.py -v
```
Expected: 5 passed (2 mutate + 3 cli).

- [ ] **Step 6: Commit**

```bash
git add src/m365ctl/mutate/label.py src/m365ctl/cli/label.py \
        scripts/ps/Set-m365ctlLabel.ps1 src/m365ctl/cli/__main__.py \
        tests/test_mutate_label.py tests/test_cli_label.py
git commit -m "feat(mutate): od-label apply/remove via PnP.PowerShell"
```

---

### Task 11: `od-undo` — reverse-op builder + executor

**Files:**
- Create: `src/m365ctl/mutate/undo.py`
- Create: `src/m365ctl/cli/undo.py`
- Create: `tests/test_mutate_undo.py`
- Create: `tests/test_cli_undo.py`
- Modify: `src/m365ctl/cli/__main__.py`

- [ ] **Step 1: Tests for `mutate/undo.py`**

Create `tests/test_mutate_undo.py`:
```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from m365ctl.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.mutate.undo import Irreversible, build_reverse_operation


def _ap(logger: AuditLogger, op_id: str, cmd: str, args: dict,
        drive_id: str, item_id: str,
        before: dict, after: dict | None, result: str,
        error: str | None = None) -> None:
    log_mutation_start(logger, op_id=op_id, cmd=cmd, args=args,
                       drive_id=drive_id, item_id=item_id, before=before)
    log_mutation_end(logger, op_id=op_id, after=after, result=result,
                     error=error)


def test_reverse_rename_restores_original_name(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="R1", cmd="od-rename",
        args={"new_name": "new.txt"}, drive_id="d", item_id="i",
        before={"parent_path": "/", "name": "old.txt"},
        after={"parent_path": "/", "name": "new.txt"}, result="ok")
    rev = build_reverse_operation(logger, "R1")
    assert rev.action == "rename"
    assert rev.args == {"new_name": "old.txt"}
    assert rev.item_id == "i"


def test_reverse_move_moves_back(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="M1", cmd="od-move",
        args={"new_parent_item_id": "B"}, drive_id="d", item_id="i",
        before={"parent_path": "/A", "name": "x", "parent_id": "A"},
        after={"parent_path": "/B", "name": "x", "parent_id": "B"},
        result="ok")
    rev = build_reverse_operation(logger, "M1")
    assert rev.action == "move"
    # We can reverse when the 'before' record contains the old parent_id or
    # path. If neither is present, reverse falls back to the recorded path.
    assert "new_parent_item_id" in rev.args or "new_parent_path" in rev.args


def test_reverse_copy_deletes_the_copy(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="C1", cmd="od-copy",
        args={"target_drive_id": "d2", "target_parent_item_id": "P",
              "new_name": "dup.txt"},
        drive_id="d1", item_id="i1",
        before={"parent_path": "/", "name": "x.txt"},
        after={"new_item_id": "NEW", "target_drive_id": "d2",
               "target_parent_item_id": "P", "new_name": "dup.txt"},
        result="ok")
    rev = build_reverse_operation(logger, "C1")
    assert rev.action == "delete"
    assert rev.drive_id == "d2"
    assert rev.item_id == "NEW"


def test_reverse_recycle_delete_is_restore(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="D1", cmd="od-delete",
        args={}, drive_id="d", item_id="i",
        before={"parent_path": "/A", "name": "x"},
        after={"parent_path": "(recycle bin)", "name": "x",
               "recycled_from": "/A"}, result="ok")
    rev = build_reverse_operation(logger, "D1")
    assert rev.action == "restore"
    assert rev.drive_id == "d"
    assert rev.item_id == "i"


def test_reverse_recycle_purge_is_irreversible(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="P1", cmd="od-clean(recycle-bin)",
        args={}, drive_id="d", item_id="i",
        before={"parent_path": "(recycle bin)", "name": "x"},
        after={"parent_path": "(permanently deleted)", "name": "x",
               "irreversible": True}, result="ok")
    with pytest.raises(Irreversible, match="permanently"):
        build_reverse_operation(logger, "P1")


def test_reverse_label_apply_is_remove(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="L1", cmd="od-label(apply)",
        args={"label": "Confidential",
              "site_url": "https://example.sharepoint.com/"},
        drive_id="d", item_id="i",
        before={"parent_path": "/", "name": "x",
                "server_relative_url": "/Documents/x"},
        after={"parent_path": "/", "name": "x", "label": "Confidential"},
        result="ok")
    rev = build_reverse_operation(logger, "L1")
    assert rev.action == "label-remove"
    assert rev.args["site_url"] == "https://example.sharepoint.com/"


def test_reverse_op_failed_originally_raises(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="F1", cmd="od-move",
        args={"new_parent_item_id": "B"}, drive_id="d", item_id="i",
        before={"parent_path": "/A", "name": "x"},
        after=None, result="error", error="accessDenied: nope")
    with pytest.raises(Irreversible, match="did not succeed"):
        build_reverse_operation(logger, "F1")


def test_reverse_unknown_op_id_raises(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    with pytest.raises(Irreversible, match="not found"):
        build_reverse_operation(logger, "nonexistent")
```

- [ ] **Step 2: Implement `mutate/undo.py`**

Create `src/m365ctl/mutate/undo.py`:
```python
"""Build reverse-ops from audit-log entries.

Reversible table:
- rename       -> rename back to ``before.name``
- move         -> move back (use ``before.parent_id`` if present, else
                  ``before.parent_path`` (best-effort))
- copy         -> delete the copy (use ``after.new_item_id`` as target)
- delete       -> restore from recycle bin
- label-apply  -> label-remove
- label-remove -> label-apply (if label recorded in ``before``)

Irreversible:
- recycle-purge (permanentDelete)
- any op whose original result != 'ok'
- share-revoke (stale-shares) — can't re-create a sharing link with the
  same id, so undo emits an Irreversible with manual-share instructions
"""
from __future__ import annotations

from m365ctl.audit import AuditLogger, find_op_by_id
from m365ctl.planfile import Operation, new_op_id


class Irreversible(RuntimeError):
    """Raised when an op cannot be automatically reversed."""


def build_reverse_operation(logger: AuditLogger, op_id: str) -> Operation:
    start, end = find_op_by_id(logger, op_id)
    if start is None or end is None:
        raise Irreversible(f"op {op_id!r} not found in audit log")
    if end.get("result") != "ok":
        raise Irreversible(
            f"op {op_id!r} did not succeed originally (result={end.get('result')!r})"
        )

    cmd = start.get("cmd", "")
    before = start.get("before", {}) or {}
    after = end.get("after", {}) or {}
    drive_id = start["drive_id"]
    item_id = start["item_id"]

    if cmd == "od-rename":
        return Operation(
            op_id=new_op_id(), action="rename",
            drive_id=drive_id, item_id=item_id,
            args={"new_name": before["name"]},
            dry_run_result=f"(undo of {op_id}) rename back to {before['name']!r}",
        )

    if cmd == "od-move":
        args: dict = {}
        if "parent_id" in before:
            args["new_parent_item_id"] = before["parent_id"]
        else:
            # Best effort — caller will need to resolve path to id.
            args["new_parent_path"] = before.get("parent_path", "/")
        return Operation(
            op_id=new_op_id(), action="move",
            drive_id=drive_id, item_id=item_id,
            args=args,
            dry_run_result=f"(undo of {op_id}) move back to "
                           f"{before.get('parent_path', '?')}",
        )

    if cmd == "od-copy":
        new_item = after.get("new_item_id")
        if not new_item:
            raise Irreversible(
                f"copy op {op_id!r} has no recorded new_item_id — cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="delete",
            drive_id=after.get("target_drive_id", drive_id),
            item_id=new_item,
            args={},
            dry_run_result=f"(undo of {op_id}) delete copy {new_item!r}",
        )

    if cmd == "od-delete":
        # Ensure the item wasn't purged afterwards — caller re-validates.
        return Operation(
            op_id=new_op_id(), action="restore",
            drive_id=drive_id, item_id=item_id,
            args={},
            dry_run_result=f"(undo of {op_id}) restore {before.get('name','?')} "
                           f"from recycle bin",
        )

    if cmd == "od-clean(recycle-bin)":
        raise Irreversible(
            f"op {op_id!r} was a recycle-bin purge — items are permanently "
            f"deleted and not recoverable by this toolkit. If retention "
            f"backup is available, contact Microsoft 365 admin."
        )

    if cmd == "od-label(apply)":
        return Operation(
            op_id=new_op_id(), action="label-remove",
            drive_id=drive_id, item_id=item_id,
            args={"site_url": start["args"]["site_url"]},
            dry_run_result=f"(undo of {op_id}) remove label "
                           f"{start['args'].get('label','?')!r}",
        )

    if cmd == "od-label(remove)":
        prior_label = before.get("label")
        if not prior_label:
            raise Irreversible(
                f"op {op_id!r} removed a label but prior label unknown"
            )
        return Operation(
            op_id=new_op_id(), action="label-apply",
            drive_id=drive_id, item_id=item_id,
            args={"site_url": start["args"]["site_url"], "label": prior_label},
            dry_run_result=f"(undo of {op_id}) re-apply {prior_label!r}",
        )

    if cmd == "od-clean(old-versions)":
        raise Irreversible(
            f"op {op_id!r} deleted file versions — version history cannot "
            f"be reconstructed. Original version content is gone."
        )

    if cmd == "od-clean(stale-shares)":
        raise Irreversible(
            f"op {op_id!r} revoked sharing link(s). Sharing links cannot be "
            f"reissued with the same URL. Re-share manually if needed."
        )

    raise Irreversible(f"no reverse-op known for cmd {cmd!r}")
```

- [ ] **Step 3: `cli/undo.py` that dispatches to the right execute_\* function**

Create `src/m365ctl/cli/undo.py`:
```python
"""`od-undo <op_id>` — replay a reverse-op from the audit log."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.audit import AuditLogger
from m365ctl.cli._common import build_graph_client
from m365ctl.cli.move import _lookup_item
from m365ctl.config import load_config
from m365ctl.mutate.delete import execute_recycle_delete, execute_restore
from m365ctl.mutate.label import execute_label_apply, execute_label_remove
from m365ctl.mutate.move import execute_move
from m365ctl.mutate.rename import execute_rename
from m365ctl.mutate.undo import Irreversible, build_reverse_operation


def run_undo(*, config_path: Path, op_id: str, confirm: bool,
             unsafe_scope: bool) -> int:
    cfg = load_config(config_path)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    try:
        rev = build_reverse_operation(logger, op_id)
    except Irreversible as e:
        print(f"irreversible: {e}", file=sys.stderr)
        return 2

    print(f"Reverse op: {rev.action} — {rev.dry_run_result}")
    if not confirm:
        print("DRY-RUN — pass --confirm to execute.")
        return 0

    graph = build_graph_client(cfg, scope=None)
    try:
        before = _lookup_item(graph, rev.drive_id, rev.item_id)
    except Exception:
        # For restore, the item is in the recycle bin and /items/{id} may 404;
        # proceed with minimal before dict.
        before = {"parent_path": "(unknown)", "name": ""}

    # Dispatch.
    if rev.action == "rename":
        r = execute_rename(rev, graph, logger, before=before)
    elif rev.action == "move":
        r = execute_move(rev, graph, logger, before=before)
    elif rev.action == "delete":
        r = execute_recycle_delete(rev, graph, logger, before=before)
    elif rev.action == "restore":
        r = execute_restore(rev, graph, logger, before=before)
    elif rev.action == "label-apply":
        r = execute_label_apply(rev, logger, before=before)
    elif rev.action == "label-remove":
        r = execute_label_remove(rev, logger, before=before)
    else:
        print(f"no executor wired for reverse action {rev.action!r}",
              file=sys.stderr)
        return 2

    if r.status != "ok":
        print(f"undo failed: {r.error}", file=sys.stderr)
        return 1
    print(f"[{rev.op_id}] ok (reverse of {op_id})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-undo")
    p.add_argument("op_id")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--unsafe-scope", action="store_true")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_undo(config_path=Path(args.config), op_id=args.op_id,
                    confirm=args.confirm, unsafe_scope=args.unsafe_scope)
```

Create `tests/test_cli_undo.py` covering:
- dry-run default prints reverse-op description without executing
- `--confirm` dispatches to the correct `execute_*` (mock them)
- irreversible ops exit 2 with a human message

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_mutate_undo.py tests/test_cli_undo.py -v
```
Expected: 11 passed (8 mutate + 3 cli).

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/mutate/undo.py src/m365ctl/cli/undo.py \
        src/m365ctl/cli/__main__.py \
        tests/test_mutate_undo.py tests/test_cli_undo.py
git commit -m "feat(mutate): od-undo with per-op reverse-op builder"
```

---

### Task 12: `bin/` wrappers + AGENTS.md update + full-suite sanity

**Files:**
- Create: `bin/od-move`, `bin/od-rename`, `bin/od-copy`, `bin/od-delete`,
  `bin/od-clean`, `bin/od-label`, `bin/od-undo`
- Modify: `AGENTS.md`

- [ ] **Step 1: Write each wrapper**

For each command, the wrapper is identical in shape. Example `bin/od-move`:
```bash
#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec uv run --project "$REPO" python -m m365ctl.cli move "$@"
```

Repeat substituting `move` for `rename`, `copy`, `delete`, `clean`, `label`, `undo`.

```bash
chmod +x bin/od-move bin/od-rename bin/od-copy bin/od-delete \
         bin/od-clean bin/od-label bin/od-undo
```

- [ ] **Step 2: Smoke-test each help**

```bash
for c in move rename copy delete clean label undo; do
  ./bin/od-$c --help >/dev/null 2>&1 && echo "$c ok" || echo "$c FAIL"
done
```
Expected: seven `ok` lines.

- [ ] **Step 3: Update `AGENTS.md` — ADD (do not rewrite) the new rows**

Extend the "Current CLI surface" table in `AGENTS.md` by appending rows. Heading becomes `## Current CLI surface (Plans 1-4 complete)`. Preserve every existing row; add:

```markdown
| `./bin/od-move --pattern <glob> --scope <s> --plan-out plan.json` | Build a move plan (dry-run). |
| `./bin/od-move --from-plan plan.json --confirm` | Execute the plan's moves. |
| `./bin/od-rename --drive-id <d> --item-id <i> --new-name <n> --confirm` | Single-item rename. |
| `./bin/od-copy --pattern <glob> --scope <s> --plan-out plan.json` | Build a copy plan (dry-run). |
| `./bin/od-copy --from-plan plan.json --confirm` | Execute the plan's copies (async polling). |
| `./bin/od-delete ... --plan-out` / `--from-plan --confirm` | Soft-delete to recycle bin. |
| `./bin/od-clean recycle-bin --scope <s>` | Hard-purge recycle bin. Not reversible. |
| `./bin/od-clean old-versions --keep N --scope <s>` | Drop all but N newest versions per item. |
| `./bin/od-clean stale-shares --older-than N --scope <s>` | Revoke sharing links older than N days. |
| `./bin/od-label apply --label <name> ...` / `od-label remove ...` | Apply/remove sensitivity label via PnP.PowerShell. |
| `./bin/od-undo <op_id> --confirm` | Replay the reverse of a past op from the audit log. |

All mutating commands:
- Dry-run by default; require `--confirm` to execute.
- Bulk ops (with `--pattern`) require the plan-file workflow — `--pattern --confirm` without `--from-plan` is rejected.
- Every mutation appends to `logs/ops/YYYY-MM-DD.jsonl` (start BEFORE the Graph call, end AFTER).
- Items outside `scope.allow_drives` require `--unsafe-scope` + a `/dev/tty` `y/N` confirm (piped stdin cannot bypass).
- Items matching `scope.deny_paths` are ALWAYS blocked — no override.
```

- [ ] **Step 4: Full-suite sanity**

```bash
uv run pytest -v
```
Expected total at this point, building on Plan 2's **52 passed + 1 skipped** baseline:
- test_planfile.py: **+6**
- test_audit.py: **+7**
- test_safety.py: **+14** (8 from Task 3 + 6 from Task 9)
- test_graph_mutations.py: **+6**
- test_mutate_move.py: **+2**, test_mutate_rename.py: **+1**
- test_mutate_copy.py: **+2**, test_mutate_delete.py: **+2**
- test_mutate_clean.py: **+3**, test_mutate_label.py: **+2**
- test_mutate_undo.py: **+8**
- test_cli_move.py: **+3**, test_cli_rename.py: **+1**
- test_cli_copy.py: **+3**, test_cli_delete.py: **+4**
- test_cli_clean.py: **+4**, test_cli_label.py: **+3**
- test_cli_undo.py: **+3**

**Total Plan 4 additions: 74**. Cumulative: **52 + 74 = 126 passed, 1 skipped** (the live auth test).

- [ ] **Step 5: Commit**

```bash
git add bin/od-move bin/od-rename bin/od-copy bin/od-delete \
        bin/od-clean bin/od-label bin/od-undo AGENTS.md
git commit -m "feat(cli): bin wrappers for mutations; AGENTS.md Plan 4 surface"
```

---

### Task 13: Live smoke test + push

This task has **no new code** — it exercises the stack end-to-end against the user's real OneDrive. Tight scope: one temp folder, one file, a round trip of every verb, observing the audit log.

- [ ] **Step 1: Pre-flight**

```bash
./bin/od-auth whoami
./bin/od-catalog-refresh --scope me
```
Expected: whoami shows delegated identity + app-only cert. Catalog refresh completes with a non-zero item count.

- [ ] **Step 2: Stage a test folder by hand**

In the OneDrive web UI (or via an `od-move` against an existing file), create an empty folder `/_m365ctl_smoke/` and note its `item_id`. Put one tiny file into it, `hello.txt`, and note its `item_id`. Export both into shell vars:

```bash
FOLDER_ID="..."   # /_m365ctl_smoke/ item id
FILE_ID="..."     # /_m365ctl_smoke/hello.txt item id
DRIVE_ID=$(./bin/od-inventory --sql "SELECT DISTINCT drive_id FROM items LIMIT 1" | tail -1)
echo "drive=$DRIVE_ID folder=$FOLDER_ID file=$FILE_ID"
```

- [ ] **Step 3: Rename + observe audit**

```bash
./bin/od-rename --drive-id "$DRIVE_ID" --item-id "$FILE_ID" \
                --new-name "hello-renamed.txt" --confirm
RENAME_OP=$(tail -1 logs/ops/$(date -u +%F).jsonl | python -c 'import sys,json; print(json.loads(sys.stdin.read())["op_id"])')
echo "rename op_id=$RENAME_OP"
```
Expected: command prints `[<uuid>] ok`; audit log gains two lines (start + end).

- [ ] **Step 4: Move to a sibling folder then move back**

Create `/_m365ctl_smoke/sub/` (or note another folder id), then:

```bash
./bin/od-move --drive-id "$DRIVE_ID" --item-id "$FILE_ID" \
              --new-parent-item-id "$SUB_FOLDER_ID" --confirm
```
Expected: one ok line, three audit entries total so far (2 rename + 2 move).

- [ ] **Step 5: Copy + delete the copy + restore**

```bash
./bin/od-copy --drive-id "$DRIVE_ID" --item-id "$FILE_ID" \
              --new-parent-item-id "$FOLDER_ID" --new-name "hello-copy.txt" \
              --confirm
# Note the new op_id; the 'after' record has new_item_id.
COPY_OP=$(tail -2 logs/ops/$(date -u +%F).jsonl | head -1 | \
          python -c 'import sys,json; print(json.loads(sys.stdin.read())["op_id"])')
./bin/od-undo "$COPY_OP" --confirm   # deletes the copy
```
Expected: copy ok; undo reports `delete copy <new_item_id>`; audit log now has start+end for copy, start+end for undo(delete).

- [ ] **Step 6: Recycle-delete + restore the original**

```bash
./bin/od-delete --drive-id "$DRIVE_ID" --item-id "$FILE_ID" --confirm
DELETE_OP=$(tail -1 logs/ops/$(date -u +%F).jsonl | \
            python -c 'import sys,json; print(json.loads(sys.stdin.read())["op_id"])')
./bin/od-undo "$DELETE_OP" --confirm   # restore from recycle bin
```
Expected: delete succeeds (file disappears from OneDrive, lands in recycle); undo restores it. Audit log has start/end for each.

- [ ] **Step 7: Purge the file for good (irreversible) and confirm undo refuses**

```bash
# first delete to recycle
./bin/od-delete --drive-id "$DRIVE_ID" --item-id "$FILE_ID" --confirm
# now purge from recycle bin
./bin/od-clean recycle-bin --scope drive:$DRIVE_ID \
               --pattern "/_m365ctl_smoke/hello-renamed.txt" \
               --plan-out /tmp/purge.json
# review /tmp/purge.json (should list exactly the one item)
./bin/od-clean recycle-bin --from-plan /tmp/purge.json --confirm
PURGE_OP=$(tail -1 logs/ops/$(date -u +%F).jsonl | \
           python -c 'import sys,json; print(json.loads(sys.stdin.read())["op_id"])')
./bin/od-undo "$PURGE_OP" --confirm
```
Expected: the final `od-undo` exits 2 with `irreversible: op ... was a recycle-bin purge — items are permanently deleted ...`.

- [ ] **Step 8: Inspect the audit log**

```bash
wc -l logs/ops/$(date -u +%F).jsonl
python -c 'import json,sys
for l in open("logs/ops/'"$(date -u +%F)"'.jsonl"):
  r = json.loads(l)
  print(r["phase"], r.get("cmd", ""), r["op_id"])'
```
Expected: pairs of start/end lines — one pair per mutation attempt, including the failed undo attempt (which itself writes nothing because build_reverse raised before any log entry).

- [ ] **Step 9: Clean up the smoke folder (by hand in web UI — we're done with it)**

- [ ] **Step 10: Full-suite one more time**

```bash
uv run pytest -v
```
Expected: **126 passed, 1 skipped**.

- [ ] **Step 11: Record completion in the plan**

Append to this file:
```markdown

---

## Completion log

- **Smoke test run:** <date>
- **Unit tests:** 126 passed + 1 live-skipped.
- **Round trip verified:** rename -> move -> copy -> undo(copy) -> delete -> undo(delete) -> delete -> recycle-purge -> undo(purge)=irreversible.
- **Audit log lines:** <N> over <M> mutations (every mutation paired start/end).
- **Irreversible flagging:** recycle-bin purge rejected by od-undo as expected.
```

Commit and push:
```bash
git add docs/superpowers/plans/2026-04-24-mutations-and-safety.md
git commit -m "chore: Plan 4 complete — mutations + safety verified live"
git push
```
Expected: all Plan 4 commits pushed to `origin/main`.

---

## Plan 4 done. What's next?

Plan 5 (Audit, Search, Workspace) picks up from here and ships the remaining spec commands:
- `od-audit-sharing` — permissions/sharing report (g in spec §2).
- `od-search` — full-text merge with Graph `/search/query` (a).
- `od-download` — materialise a subset locally (f).
- `od-sync-workspace` — rclone bisync wrapper for hybrid local workflows.

Plan 5 reuses `safety.assert_scope_allowed` for `od-download` (no mutation but honours deny_paths), the audit log for read-op attribution of any item it touches, and the plan-file schema for `od-download --from-plan`.

---

## Completion log

- **Smoke test run:** 2026-04-24 (Arda's workstation, agentic driver).
- **Unit tests:** 190 passed, 1 skipped (live-gated `test_auth.py::test_live_whoami`).
- **Staging (Step 2):** Created `/_m365ctl_smoke/` folder, `/_m365ctl_smoke/sub/`, and `/_m365ctl_smoke/hello.txt` directly via Graph `POST /me/drive/root/children` and `PUT /content` (the toolkit has no create-folder verb — deliberate; folder creation is a Plan-5 or manual op).
- **Step 3 rename → ok.** `hello.txt` → `hello-renamed.txt`. One paired start/end in `logs/ops/2026-04-24.jsonl`. `before.name` and `after.name` both correct.
- **Step 4 move → ok.** Moved into `/_m365ctl_smoke/sub/`. Graph returned new `parentReference.id` as expected.
- **Step 5 copy + undo(delete the copy) → ok.** Copy executed synchronously (Graph returned 200 with new item id; monitor-URL polling never triggered for this small file). `od-undo <copy_op_id>` built a `delete` against the new item id from `after.new_item_id` and deleted the copy cleanly. Two more paired audit entries.
- **Step 6 recycle-delete → ok; undo(restore) → error.**
    - `od-delete` against the original item returned 204 and logged `ok (recycled)`.
    - `od-undo <delete_op_id>` built the correct `restore` op, but Graph responded `notSupported: Operation not supported` to `POST /drives/{d}/items/{i}/restore`. **This is a Plan 4 design gap:** Microsoft Graph v1.0's `/restore` verb on a `driveItem` is not universally supported for OneDrive-for-Business recycle-bin items — the recycle-bin representation may need a different ID (recycleBinItem) than the pre-delete `item_id`. The audit log correctly records the failed attempt (start + end with `result=error`); the CLI returned 1.
- **Step 7 purge via `od-clean recycle-bin` → error; undo → irreversible (exit 2).**
    - Had to hand-craft a plan file because the catalog has no knowledge of recycle-bin items (catalog only snapshots the live drive). Fed `--from-plan /tmp/purge.json` with a single `recycle-purge` op.
    - First attempt **crashed** because `cli/clean.py` ran `_lookup_item` before dispatching, and recycle-bin items 404 at `GET /drives/{d}/items/{i}`. **Fixed in commit `802c5b9`:** fall back to minimal metadata on 404, same pattern as `cli/undo.py`.
    - Second attempt ran the purge: Graph returned `itemNotFound: Item not found` on `POST .../permanentDelete`. **Another Graph-shape issue** — `permanentDelete` also wants the recycle-bin-specific id, not the original.
    - `od-undo <purge_op_id>` correctly raised `Irreversible` — not because the op was a purge (that branch never took because the purge errored), but via the generic "did not succeed originally (result='error')" guard. **Exit 2 matches spec.** Irreversibility gate verified.
- **Step 8 audit log inspection:** 14 lines, 7 paired `start/end` sets covering all attempts above. `result` field correctly reflects ok/error for each. No orphaned start-without-end.
- **Step 9 clean up:** `_m365ctl_smoke/` folder removed via direct Graph `DELETE /me/drive/items/{folder_id}` (soft-recycle). Workspaces clean. `git status --porcelain` clean after commits.
- **Step 10 full-suite:** **190 passed, 1 skipped** on final run — 7 more passing than the plan's predicted 183, because Plan 3 Task 12 added 3 regression tests (`test_resolve_scope_tenant_skips_{resourcenotfound_mysite,notallowed_access_blocked}` and the pre-existing Task 11 adversarial extras).
- **Step 11 completion log:** this section.
- **Step 12 push:** held for user approval.

### Plan 4 bugs discovered during live smoke (beyond the Plan 3 fixes)

1. **`cli/clean.py` crashed on recycle-bin lookups.** (`802c5b9`) — `GET /drives/{d}/items/{i}` 404s for items in the recycle bin; we were calling that unconditionally before the purge. Falls back to minimal metadata now, parallel to how `cli/undo.py` handles it.

### Plan 4 design gaps (defer to a follow-up plan)

2. **`/restore` doesn't universally work on OneDrive-for-Business recycle-bin items.** `execute_restore` in `mutate/delete.py` issues `POST /drives/{d}/items/{i}/restore` using the pre-delete item_id, which Graph rejects with `notSupported`. The correct endpoint is either the SharePoint REST recycle-bin API (`/Web/RecycleBin('<rb_id>')/Restore()`) or a Graph endpoint that takes the recycle-bin-item id (which requires looking up `/drives/{d}/recycleBin` first). Current unit tests pass because they mock Graph returning 200; they never caught this. **Fix direction:** add a `_lookup_recycle_bin_id(original_item_id)` that queries `/drives/{d}/recycleBin` and finds the matching entry, then use that id in `/restore`. Not in scope for Plan 4's landing.
3. **`permanentDelete` has the same shape issue.** `purge_recycle_bin_item` calls `POST /drives/{d}/items/{i}/permanentDelete` with the pre-delete id → 404. Same fix as #2: resolve the recycle-bin id first.
4. **`allow_drives = ["me"]` is a dead token** (carried over from Plan 3 log). `safety._drive_allowed` string-compares; `"me"` is never a real drive_id. Resolve at config-load time via the delegated identity's drive id, or expand lazily in `_drive_allowed`.
5. **`od-clean --pattern` won't find recycle-bin items** because the catalog only indexes live items. Either document this (`recycle-bin` subcommand only accepts `--from-plan` against hand-crafted plans; bulk purge of the recycle bin needs a separate "list recycle bin" call) or extend `expand_pattern` to optionally query `/drives/{d}/recycleBin`. Defer.

### Safety properties VERIFIED during live run

- Spec §7 rule 1: `--confirm` genuinely gates execution — every step above used `--confirm`; removing it (not tested here but covered by unit test `test_dry_run_is_default_no_mutation`) would have produced dry-run output.
- Spec §7 rule 5: audit log has **every** mutation attempt, with paired start/end, even when the Graph call errored. Start records persist BEFORE the call — verified by the audit dump above.
- Spec §7 rule 6: no hard deletes happened via `od-delete`. Every delete was a soft-recycle (Graph `DELETE /drives/{d}/items/{i}`); hard delete only tried via the explicit `od-clean recycle-bin` subcommand. `cmd` field in the audit log distinguishes them clearly (`od-delete` vs `od-clean(recycle-bin)`).
- Spec §7 rule 8 (irreversible flagging): `od-undo` on a failed op returned exit 2 with "irreversible", matching the spec. The specific branch covered was "did not succeed originally" (via the Irreversible guard on `result != 'ok'`); the "recycle-purge can't be undone" branch is unit-tested but not live-verified because the live purge itself errored (see design gap #3).

