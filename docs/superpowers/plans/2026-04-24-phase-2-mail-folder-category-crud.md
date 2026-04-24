# Phase 2 — Mail Folder CRUD + Categories CRUD (0.3.0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the first mail mutations — folder create/rename/move/delete (soft) + master-category add/update/remove/sync — wired through the same audit + undo Dispatcher infrastructure that the OneDrive mutations use. Bumps version to 0.3.0.

**Architecture:**
- New modules `src/m365ctl/mail/mutate/folders.py` + `src/m365ctl/mail/mutate/categories.py` mirror the shape of `src/m365ctl/onedrive/mutate/rename.py` (dataclass `Result`, `execute_*` function with `(op, graph, logger, *, before)` signature, `log_mutation_start`/`log_mutation_end` wrapping the Graph call).
- Mail mutations reuse the **existing** `Operation` dataclass from `common/planfile.py` — `drive_id` holds the `mailbox_upn` (or `"me"`), `item_id` holds the `folder_id` or `category_id`. Semantic mapping only; no schema change.
- `_VALID_ACTIONS` extends with the `mail.folder.*` and `mail.categories.*` action names (spec §11.4).
- `mail.mutate.undo` mirrors `onedrive.mutate.undo`: a `build_reverse_mail_operation(logger, op_id)` function + Dispatcher registration via `register_mail_inverses(dispatcher)`. Wired into `m365ctl.cli.undo` so `m365ctl undo <op-id>` routes mail ops the same way as `od.*` ops.
- CLI `m365ctl mail folders` and `m365ctl mail categories` gain subcommands (`create/rename/move/delete` and `add/update/remove/sync`). Each mutation subcommand requires `--confirm`; dry-run is the default. Folder path → id resolution uses `mail.folders.resolve_folder_path`. Hard-coded deny list (`is_folder_denied`) blocks mutations on compliance folders with NO Graph call.

**Tech Stack:** Python 3.11+ stdlib, httpx, msal (unchanged). Tests use `pytest` + `unittest.mock.MagicMock`. No new dependencies.

**Parent spec:** `docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md` — §11.2 (deny folders), §11.4 (action namespace), §12.1 (per-action capture table), §19 Phase 2 (deliverables + acceptance).

**Safety posture:**
- **`--confirm` is required** for every mutation. Ad-hoc dry-run is the default.
- **Plan-file bulk** path (`--from-plan <path> --confirm`) is accepted for folder move/delete (per spec acceptance); ad-hoc individual args are the primary entry for create/rename.
- **`is_folder_denied` fires BEFORE any Graph call.** Compliance folders (Recoverable Items, Purges, Audits, Calendar, Contacts, Tasks, Notes) are absolutely blocked.
- **`assert_mailbox_allowed` gates every mutation** per the CLI `--mailbox` flag.
- **No force-push; no `git add -A` sweep.** Work on feature branch `phase-2-mail-folder-category-crud` (off `main`).

---

## File Structure (Phase 2 target)

```
m365ctl/
├── pyproject.toml                          # MODIFIED — version 0.3.0
├── CHANGELOG.md                            # MODIFIED — [0.3.0] entry
├── src/m365ctl/
│   ├── common/
│   │   └── planfile.py                     # MODIFIED — extend _VALID_ACTIONS with mail.folder.* + mail.categories.*
│   ├── cli/
│   │   └── undo.py                         # MODIFIED — register mail inverses alongside od inverses
│   └── mail/
│       ├── cli/
│       │   ├── folders.py                  # MODIFIED — add create/rename/move/delete subcommands
│       │   └── categories.py               # MODIFIED — add add/update/remove/sync subcommands
│       └── mutate/
│           ├── __init__.py                 # unchanged (Phase 0 scaffold)
│           ├── _common.py                  # NEW — shared mutation helpers (Result base, mailbox-scope guard, plan-file adapter)
│           ├── folders.py                  # NEW — execute_{create,rename,move,delete}_folder
│           ├── categories.py               # NEW — execute_{add,update,remove}_category + sync_master_categories
│           └── undo.py                     # NEW — build_reverse_mail_operation + register_mail_inverses
└── tests/
    ├── test_mail_mutate_folders.py         # NEW
    ├── test_mail_mutate_categories.py      # NEW
    ├── test_mail_mutate_undo.py            # NEW
    ├── test_cli_mail_folders_mutate.py     # NEW — subcommand parser + e2e-style tests
    └── test_cli_mail_categories_mutate.py  # NEW
```

---

## Preflight

### Task 0: Branch + baseline

- [ ] **Step 1:** `git status` → clean. `git branch --show-current` → `main`.
- [ ] **Step 2:** `git checkout -b phase-2-mail-folder-category-crud`
- [ ] **Step 3:** `uv run pytest -m "not live" -q 2>&1 | tail -3` → record baseline (expected **370 passed, 1 deselected**).

---

## Group 1: Planfile extension

### Task 1: Add mail.* actions to `_VALID_ACTIONS` + `Action` Literal

**Files:**
- Modify: `src/m365ctl/common/planfile.py`
- Modify: `tests/test_planfile.py`

- [ ] **Step 1: Failing test — mail actions accepted by the loader.**

Append to `tests/test_planfile.py`:
```python
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
```

- [ ] **Step 2:** `uv run pytest tests/test_planfile.py -q` → new tests FAIL (unknown action).

- [ ] **Step 3: Extend `_VALID_ACTIONS` + `Action` Literal in `src/m365ctl/common/planfile.py`.**

Find the existing `_VALID_ACTIONS` frozenset and `Action` Literal. Add the mail actions (spec §11.4) — keep the existing `od.*` + legacy bare entries intact.

New entries (add to BOTH the `Action` Literal and the `_VALID_ACTIONS` set):
```
"mail.folder.create", "mail.folder.rename",
"mail.folder.move", "mail.folder.delete",
"mail.categories.add", "mail.categories.update",
"mail.categories.remove",
```

(Phase 2 ships only these 7 mail actions. The rest of the mail.* namespace — mail.move, mail.flag, mail.send, etc. — arrives in Phase 3+. Don't preemptively add them; land them with the code that emits them.)

- [ ] **Step 4:** `uv run pytest tests/test_planfile.py -q` → all pass.

- [ ] **Step 5:** `uv run pytest -m "not live" -q 2>&1 | tail -3` → baseline + 2 new = 372.

- [ ] **Step 6: Commit.**
```bash
git add src/m365ctl/common/planfile.py tests/test_planfile.py
git commit -m "feat(planfile): accept mail.folder.* + mail.categories.* action namespaces"
```

---

## Group 2: Mail mutation helpers

### Task 2: `mail/mutate/_common.py` — result dataclass + guard helper

**Files:**
- Create: `src/m365ctl/mail/mutate/_common.py`

- [ ] **Step 1: Write the module** (no direct tests — indirectly exercised by Tasks 3–5).

```python
"""Shared helpers for mail mutation executors.

The three exports are:

- ``MailResult`` — standard return type for every ``execute_*`` mail
  mutation. Shape mirrors ``onedrive.mutate.rename.RenameResult`` so CLI
  handlers can treat OneDrive + Mail results uniformly.
- ``assert_mail_target_allowed`` — hardens the CLI layer. Runs the two
  mailbox + folder gates that every mail mutation must pass BEFORE any
  Graph call.
- ``derive_mailbox_upn`` — canonicalize ``--mailbox`` spec to the
  ``mailbox_upn`` stored in audit records.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from m365ctl.common.config import Config
from m365ctl.common.safety import (
    ScopeViolation,
    assert_mailbox_allowed,
    is_folder_denied,
)


@dataclass(frozen=True)
class MailResult:
    op_id: str
    status: str
    error: str | None = None
    after: dict[str, Any] | None = None


def derive_mailbox_upn(mailbox_spec: str) -> str:
    """Return the address-or-keyword stored as ``drive_id`` in audit records."""
    if mailbox_spec == "me":
        return "me"
    if mailbox_spec.startswith("upn:") or mailbox_spec.startswith("shared:"):
        return mailbox_spec.split(":", 1)[1]
    return mailbox_spec


def assert_mail_target_allowed(
    cfg: Config,
    *,
    mailbox_spec: str,
    auth_mode: str,
    unsafe_scope: bool,
    folder_path: str | None = None,
) -> None:
    """Combined mailbox + folder gate for mail mutations.

    Order matters: folder deny check runs first (absolute, never overridable),
    then mailbox scope. The CLI layer calls this before any Graph call.

    Raises ``ScopeViolation`` on any violation.
    """
    if folder_path is not None and is_folder_denied(folder_path, cfg):
        raise ScopeViolation(
            f"folder {folder_path!r} matches a deny pattern "
            f"(compliance or scope.deny_folders); mutation blocked"
        )
    assert_mailbox_allowed(
        mailbox_spec, cfg, auth_mode=auth_mode, unsafe_scope=unsafe_scope,
    )
```

- [ ] **Step 2:** `uv run python -c "from m365ctl.mail.mutate._common import MailResult, assert_mail_target_allowed, derive_mailbox_upn; print('ok')"` → `ok`.

- [ ] **Step 3: Commit.**
```bash
git add src/m365ctl/mail/mutate/_common.py
git commit -m "feat(mail/mutate): _common — MailResult, assert_mail_target_allowed, derive_mailbox_upn"
```

---

## Group 3: Folder mutations (4 verbs)

### Task 3: `mail/mutate/folders.py` — create, rename, move, delete

**Files:**
- Create: `src/m365ctl/mail/mutate/folders.py`
- Create: `tests/test_mail_mutate_folders.py`

- [ ] **Step 1: Failing tests.**

Write `tests/test_mail_mutate_folders.py`:
```python
"""Tests for m365ctl.mail.mutate.folders — mocked Graph + AuditLogger."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.folders import (
    execute_create_folder,
    execute_delete_folder,
    execute_move_folder,
    execute_rename_folder,
)


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def _graph() -> MagicMock:
    return MagicMock()


# ---- create_folder ---------------------------------------------------------

def test_create_folder_posts_and_records_after(tmp_path):
    graph = _graph()
    graph.post.return_value = {"id": "new-folder-id", "displayName": "Triage"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-create-1",
        action="mail.folder.create",
        drive_id="me",
        item_id="inbox",              # parent folder id
        args={"name": "Triage", "parent_path": "/Inbox"},
    )
    result = execute_create_folder(op, graph, logger, before={})
    assert result.status == "ok"
    assert result.after == {"id": "new-folder-id", "path": "/Inbox/Triage"}
    assert graph.post.call_args.args[0] == "/me/mailFolders/inbox/childFolders"
    assert graph.post.call_args.kwargs["json"] == {"displayName": "Triage"}

    entries = list(iter_audit_entries(logger))
    assert [e["phase"] for e in entries] == ["start", "end"]
    assert entries[0]["cmd"] == "mail-folder-create"
    assert entries[1]["after"] == {"id": "new-folder-id", "path": "/Inbox/Triage"}


def test_create_folder_root_level(tmp_path):
    graph = _graph()
    graph.post.return_value = {"id": "top", "displayName": "Archive"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-2",
        action="mail.folder.create",
        drive_id="me",
        item_id="",                   # empty parent_id → top-level
        args={"name": "Archive", "parent_path": ""},
    )
    result = execute_create_folder(op, graph, logger, before={})
    assert result.status == "ok"
    assert graph.post.call_args.args[0] == "/me/mailFolders"


def test_create_folder_graph_error(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = _graph()
    graph.post.side_effect = GraphError("conflict: folder exists")
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-3",
        action="mail.folder.create",
        drive_id="me",
        item_id="inbox",
        args={"name": "Dup", "parent_path": "/Inbox"},
    )
    result = execute_create_folder(op, graph, logger, before={})
    assert result.status == "error"
    assert "conflict" in (result.error or "")


# ---- rename_folder ---------------------------------------------------------

def test_rename_folder_patches_and_records_before(tmp_path):
    graph = _graph()
    graph.patch.return_value = {"id": "f1", "displayName": "Triaged"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-rename",
        action="mail.folder.rename",
        drive_id="me",
        item_id="f1",
        args={"new_name": "Triaged"},
    )
    result = execute_rename_folder(op, graph, logger,
                                   before={"display_name": "Triage", "path": "/Inbox/Triage"})
    assert result.status == "ok"
    assert result.after == {"display_name": "Triaged"}
    assert graph.patch.call_args.args[0] == "/me/mailFolders/f1"
    assert graph.patch.call_args.kwargs["json_body"] == {"displayName": "Triaged"}

    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-folder-rename"
    assert entries[0]["before"] == {"display_name": "Triage", "path": "/Inbox/Triage"}


# ---- move_folder -----------------------------------------------------------

def test_move_folder_posts_move_and_records_before(tmp_path):
    graph = _graph()
    graph.post.return_value = {"id": "f1", "parentFolderId": "archive"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-move",
        action="mail.folder.move",
        drive_id="me",
        item_id="f1",
        args={"destination_id": "archive", "destination_path": "/Archive"},
    )
    result = execute_move_folder(op, graph, logger,
                                 before={"parent_id": "inbox", "path": "/Inbox/Triage"})
    assert result.status == "ok"
    assert result.after == {"parent_id": "archive", "path": "/Archive"}
    assert graph.post.call_args.args[0] == "/me/mailFolders/f1/move"
    assert graph.post.call_args.kwargs["json"] == {"destinationId": "archive"}


# ---- delete_folder ---------------------------------------------------------

def test_delete_folder_calls_delete_and_records_before(tmp_path):
    graph = _graph()
    graph.delete.return_value = None
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-delete",
        action="mail.folder.delete",
        drive_id="me",
        item_id="f1",
        args={},
    )
    result = execute_delete_folder(
        op, graph, logger,
        before={
            "id": "f1", "display_name": "Triage", "path": "/Inbox/Triage",
            "parent_id": "inbox", "total_items": 7, "unread_items": 2,
            "child_folder_count": 0,
        },
    )
    assert result.status == "ok"
    assert result.after is None     # nothing to record post-delete
    assert graph.delete.call_args.args[0] == "/me/mailFolders/f1"

    entries = list(iter_audit_entries(logger))
    assert entries[0]["before"]["display_name"] == "Triage"


# ---- app-only routing ------------------------------------------------------

def test_create_folder_app_only_routes_via_users_upn(tmp_path):
    graph = _graph()
    graph.post.return_value = {"id": "x", "displayName": "Y"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-ao",
        action="mail.folder.create",
        drive_id="bob@example.com",       # app-only: drive_id holds UPN
        item_id="inbox",
        args={"name": "Y", "parent_path": "/Inbox", "auth_mode": "app-only"},
    )
    execute_create_folder(op, graph, logger, before={})
    assert graph.post.call_args.args[0] == "/users/bob@example.com/mailFolders/inbox/childFolders"


# ---- from graph-shape failure modes ----------------------------------------

def test_rename_folder_missing_new_name_raises(tmp_path):
    graph = _graph()
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-bad",
        action="mail.folder.rename",
        drive_id="me", item_id="f1",
        args={},
    )
    with pytest.raises(KeyError):
        execute_rename_folder(op, graph, logger, before={"display_name": "X"})
```

- [ ] **Step 2:** `uv run pytest tests/test_mail_mutate_folders.py -q` → all FAIL (module missing).

- [ ] **Step 3: Implement `src/m365ctl/mail/mutate/folders.py`.**

```python
"""Folder CRUD mutations — create, rename, move, delete (soft).

All four ``execute_*`` functions:
- Take ``(op, graph, logger, *, before)`` like ``onedrive.mutate.rename.execute_rename``.
- Emit ``log_mutation_start`` with the ``before`` block, call Graph, emit
  ``log_mutation_end`` with ``after``.
- Return a ``MailResult`` (see ``_common.py``).

``op.drive_id`` holds the mailbox UPN (or the literal ``"me"``). ``op.item_id``
holds the parent folder id (for create) or the target folder id (for
rename/move/delete). The CLI layer populates these via
``mail.folders.resolve_folder_path``.

``op.args["auth_mode"]`` distinguishes delegated vs app-only and selects
``/me`` vs ``/users/{upn}`` routing. Default: delegated.
"""
from __future__ import annotations

from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.mutate._common import MailResult


def _user_base(op: Operation) -> str:
    """Resolve the Graph URL prefix from ``op.drive_id`` + ``op.args``."""
    auth_mode = op.args.get("auth_mode", "delegated")
    # Reconstruct a mailbox spec: ``"me"`` or ``"upn:<addr>"`` (audit records
    # store UPN under drive_id as a bare string).
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)


def execute_create_folder(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /mailFolders (or .../{parent}/childFolders) with {displayName}."""
    name = op.args["name"]
    parent_id = op.item_id
    parent_path = op.args.get("parent_path", "") or ""
    ub = _user_base(op)

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-folder-create",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        path = f"{ub}/mailFolders" if not parent_id else f"{ub}/mailFolders/{parent_id}/childFolders"
        created = graph.post(path, json={"displayName": name})
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))

    new_path = f"{parent_path}/{name}" if parent_path else name
    after: dict[str, Any] = {"id": created.get("id", ""), "path": new_path}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_rename_folder(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """PATCH /mailFolders/{id} with {displayName}."""
    new_name = op.args["new_name"]
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-folder-rename",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        result = graph.patch(
            f"{ub}/mailFolders/{op.item_id}",
            json_body={"displayName": new_name},
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"display_name": result.get("displayName", new_name)}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_move_folder(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /mailFolders/{id}/move with {destinationId}."""
    dest_id = op.args["destination_id"]
    dest_path = op.args.get("destination_path", "") or ""
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-folder-move",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.post(
            f"{ub}/mailFolders/{op.item_id}/move",
            json={"destinationId": dest_id},
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"parent_id": dest_id, "path": dest_path}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_delete_folder(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """DELETE /mailFolders/{id} (Graph moves it to Deleted Items — soft delete)."""
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-folder-delete",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.delete(f"{ub}/mailFolders/{op.item_id}")
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    log_mutation_end(logger, op_id=op.op_id, after=None, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=None)
```

- [ ] **Step 4:** `uv run pytest tests/test_mail_mutate_folders.py -q` → all pass.

- [ ] **Step 5:** `uv run pytest -m "not live" -q 2>&1 | tail -3` → 372 + 8 = 380.

- [ ] **Step 6: Commit.**
```bash
git add src/m365ctl/mail/mutate/folders.py tests/test_mail_mutate_folders.py
git commit -m "feat(mail/mutate): folders — create/rename/move/delete executors with audit + MailResult"
```

---

## Group 4: Category mutations (4 verbs)

### Task 4: `mail/mutate/categories.py` — add, update, remove, sync

**Files:**
- Create: `src/m365ctl/mail/mutate/categories.py`
- Create: `tests/test_mail_mutate_categories.py`

- [ ] **Step 1: Failing tests.**

Write `tests/test_mail_mutate_categories.py`:
```python
"""Tests for m365ctl.mail.mutate.categories."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.models import Category
from m365ctl.mail.mutate.categories import (
    compute_sync_plan,
    execute_add_category,
    execute_remove_category,
    execute_update_category,
)


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def _graph() -> MagicMock:
    return MagicMock()


# ---- add -------------------------------------------------------------------

def test_add_category_posts_and_records_after(tmp_path):
    graph = _graph()
    graph.post.return_value = {"id": "new-cat", "displayName": "Waiting", "color": "preset0"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-add",
        action="mail.categories.add",
        drive_id="me",
        item_id="",                     # no target id — we're creating
        args={"name": "Waiting", "color": "preset0"},
    )
    result = execute_add_category(op, graph, logger, before={})
    assert result.status == "ok"
    assert result.after == {"id": "new-cat", "display_name": "Waiting", "color": "preset0"}
    assert graph.post.call_args.args[0] == "/me/outlook/masterCategories"
    assert graph.post.call_args.kwargs["json"] == {"displayName": "Waiting", "color": "preset0"}


# ---- update ----------------------------------------------------------------

def test_update_category_patches_and_records_before(tmp_path):
    graph = _graph()
    graph.patch.return_value = {"id": "c1", "displayName": "Waiting-New", "color": "preset2"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-update",
        action="mail.categories.update",
        drive_id="me",
        item_id="c1",
        args={"name": "Waiting-New", "color": "preset2"},
    )
    result = execute_update_category(
        op, graph, logger,
        before={"display_name": "Waiting", "color": "preset0"},
    )
    assert result.status == "ok"
    assert result.after == {"display_name": "Waiting-New", "color": "preset2"}
    assert graph.patch.call_args.args[0] == "/me/outlook/masterCategories/c1"
    assert graph.patch.call_args.kwargs["json_body"] == {
        "displayName": "Waiting-New", "color": "preset2",
    }


def test_update_category_partial_name_only(tmp_path):
    """--name only (no color) must not zero the color field."""
    graph = _graph()
    graph.patch.return_value = {"id": "c1", "displayName": "X"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-partial",
        action="mail.categories.update",
        drive_id="me",
        item_id="c1",
        args={"name": "X"},             # no color key
    )
    execute_update_category(op, graph, logger, before={"display_name": "W", "color": "preset0"})
    assert graph.patch.call_args.kwargs["json_body"] == {"displayName": "X"}


# ---- remove ----------------------------------------------------------------

def test_remove_category_deletes_and_records_before(tmp_path):
    graph = _graph()
    graph.delete.return_value = None
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-remove",
        action="mail.categories.remove",
        drive_id="me",
        item_id="c1",
        args={},
    )
    result = execute_remove_category(
        op, graph, logger,
        before={"display_name": "Waiting", "color": "preset0", "messages_with_category": []},
    )
    assert result.status == "ok"
    assert result.after is None
    assert graph.delete.call_args.args[0] == "/me/outlook/masterCategories/c1"

    entries = list(iter_audit_entries(logger))
    assert entries[0]["before"]["display_name"] == "Waiting"


# ---- sync ------------------------------------------------------------------

def test_compute_sync_plan_add_missing():
    live = [
        Category(id="c1", display_name="Followup", color="preset0"),
    ]
    desired = ["Followup", "Waiting", "Done"]
    plan = compute_sync_plan(live, desired, default_color="preset1")
    assert len(plan) == 2
    actions = [op["action"] for op in plan]
    assert actions == ["mail.categories.add", "mail.categories.add"]
    names = [op["args"]["name"] for op in plan]
    assert names == ["Waiting", "Done"]
    assert all(op["args"]["color"] == "preset1" for op in plan)


def test_compute_sync_plan_no_removal_of_extras():
    """sync only ADDS — never removes user-created categories not in config."""
    live = [
        Category(id="c1", display_name="Followup", color="preset0"),
        Category(id="c2", display_name="LegacyUserCat", color="preset3"),
    ]
    desired = ["Followup"]
    plan = compute_sync_plan(live, desired, default_color="preset0")
    assert plan == []


def test_compute_sync_plan_case_insensitive_match():
    live = [Category(id="c1", display_name="followup", color="preset0")]
    desired = ["Followup"]
    plan = compute_sync_plan(live, desired, default_color="preset0")
    assert plan == []
```

- [ ] **Step 2:** `uv run pytest tests/test_mail_mutate_categories.py -q` → all FAIL.

- [ ] **Step 3: Implement `src/m365ctl/mail/mutate/categories.py`.**

```python
"""Master-category mutations — add, update, remove, sync.

``sync`` is a pure function: given a live category list and a desired list,
it returns a list of ``mail.categories.add`` op specs to bring the live set
up to the desired one. It NEVER emits ``remove`` ops — the spec (§19 Phase 2
acceptance) says sync reconciles toward the config set, but removing
user-created categories not in config would surprise users.
"""
from __future__ import annotations

from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.models import Category
from m365ctl.mail.mutate._common import MailResult


def _user_base(op: Operation) -> str:
    auth_mode = op.args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)


def execute_add_category(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /outlook/masterCategories with {displayName, color}."""
    name = op.args["name"]
    color = op.args.get("color", "preset0")
    ub = _user_base(op)

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-categories-add",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        created = graph.post(
            f"{ub}/outlook/masterCategories",
            json={"displayName": name, "color": color},
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {
        "id": created.get("id", ""),
        "display_name": created.get("displayName", name),
        "color": created.get("color", color),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_update_category(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """PATCH /outlook/masterCategories/{id} with {displayName?, color?}."""
    ub = _user_base(op)
    payload: dict[str, Any] = {}
    if "name" in op.args:
        payload["displayName"] = op.args["name"]
    if "color" in op.args:
        payload["color"] = op.args["color"]

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-categories-update",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        result = graph.patch(
            f"{ub}/outlook/masterCategories/{op.item_id}",
            json_body=payload,
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {}
    if "displayName" in result:
        after["display_name"] = result["displayName"]
    if "color" in result:
        after["color"] = result["color"]
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_remove_category(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """DELETE /outlook/masterCategories/{id}."""
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-categories-remove",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.delete(f"{ub}/outlook/masterCategories/{op.item_id}")
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    log_mutation_end(logger, op_id=op.op_id, after=None, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=None)


def compute_sync_plan(
    live: list[Category],
    desired: list[str],
    *,
    default_color: str = "preset0",
) -> list[dict[str, Any]]:
    """Return a list of ``mail.categories.add`` op-spec dicts for names
    in ``desired`` but not in ``live``. Never emits removals.

    Matching is case-insensitive on display name.
    """
    have = {c.display_name.casefold() for c in live}
    plan: list[dict[str, Any]] = []
    for name in desired:
        if name.casefold() in have:
            continue
        plan.append({
            "op_id": new_op_id(),
            "action": "mail.categories.add",
            "drive_id": "me",
            "item_id": "",
            "args": {"name": name, "color": default_color},
        })
    return plan
```

- [ ] **Step 4:** `uv run pytest tests/test_mail_mutate_categories.py -q` → pass.

- [ ] **Step 5:** `uv run pytest -m "not live" -q 2>&1 | tail -3` → 380 + 6 = 386.

- [ ] **Step 6: Commit.**
```bash
git add src/m365ctl/mail/mutate/categories.py tests/test_mail_mutate_categories.py
git commit -m "feat(mail/mutate): categories — add/update/remove executors + pure compute_sync_plan"
```

---

## Group 5: Mail undo — reverse-op builder + Dispatcher registration

### Task 5: `mail/mutate/undo.py` — build_reverse_mail_operation + register_mail_inverses

**Files:**
- Create: `src/m365ctl/mail/mutate/undo.py`
- Create: `tests/test_mail_mutate_undo.py`

Spec §12.1 per-action capture table (Phase 2 entries):
- `mail.folder.create` → inverse `mail.folder.delete` (soft)
- `mail.folder.rename` → rename back to `before.display_name`
- `mail.folder.move` → move back to `before.parent_id`
- `mail.folder.delete` → **irreversible** in Phase 2 (Graph moves to "Deleted Items" folder, but restoring folders from there needs manual intervention; Phase 4 adds the restore path for messages).
- `mail.categories.add` → inverse `mail.categories.remove` using `after.id`
- `mail.categories.update` → update back using `before.display_name` + `before.color`
- `mail.categories.remove` → inverse `mail.categories.add` using `before.display_name` + `before.color`; message-category links are lost (documented).

- [ ] **Step 1: Failing tests.**

Write `tests/test_mail_mutate_undo.py`:
```python
"""Tests for m365ctl.mail.mutate.undo — reverse-op building + Dispatcher wiring."""
from __future__ import annotations

from pathlib import Path

import pytest

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.undo import Dispatcher, IrreversibleOp
from m365ctl.mail.mutate.undo import (
    build_reverse_mail_operation,
    register_mail_inverses,
)
from m365ctl.onedrive.mutate.undo import Irreversible


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def _record_mutation(logger, *, op_id, cmd, drive_id, item_id, args, before, after):
    log_mutation_start(logger, op_id=op_id, cmd=cmd, args=args,
                       drive_id=drive_id, item_id=item_id, before=before)
    log_mutation_end(logger, op_id=op_id, after=after, result="ok")


# ---- folder inverses -------------------------------------------------------

def test_reverse_mail_folder_create_emits_delete(tmp_path):
    logger = _logger(tmp_path)
    _record_mutation(
        logger, op_id="op-1", cmd="mail-folder-create",
        drive_id="me", item_id="inbox",
        args={"name": "Triage", "parent_path": "/Inbox"},
        before={},
        after={"id": "new-folder", "path": "/Inbox/Triage"},
    )
    rev = build_reverse_mail_operation(logger, "op-1")
    assert rev.action == "mail.folder.delete"
    assert rev.drive_id == "me"
    assert rev.item_id == "new-folder"


def test_reverse_mail_folder_rename_emits_rename_back(tmp_path):
    logger = _logger(tmp_path)
    _record_mutation(
        logger, op_id="op-2", cmd="mail-folder-rename",
        drive_id="me", item_id="f1",
        args={"new_name": "Triaged"},
        before={"display_name": "Triage", "path": "/Inbox/Triage"},
        after={"display_name": "Triaged"},
    )
    rev = build_reverse_mail_operation(logger, "op-2")
    assert rev.action == "mail.folder.rename"
    assert rev.args == {"new_name": "Triage"}


def test_reverse_mail_folder_move_emits_move_back(tmp_path):
    logger = _logger(tmp_path)
    _record_mutation(
        logger, op_id="op-3", cmd="mail-folder-move",
        drive_id="me", item_id="f1",
        args={"destination_id": "archive", "destination_path": "/Archive"},
        before={"parent_id": "inbox", "path": "/Inbox/Triage"},
        after={"parent_id": "archive", "path": "/Archive"},
    )
    rev = build_reverse_mail_operation(logger, "op-3")
    assert rev.action == "mail.folder.move"
    assert rev.args["destination_id"] == "inbox"


def test_reverse_mail_folder_delete_is_irreversible(tmp_path):
    logger = _logger(tmp_path)
    _record_mutation(
        logger, op_id="op-4", cmd="mail-folder-delete",
        drive_id="me", item_id="f1",
        args={}, before={"display_name": "Triage"}, after=None,
    )
    with pytest.raises(Irreversible):
        build_reverse_mail_operation(logger, "op-4")


# ---- category inverses -----------------------------------------------------

def test_reverse_mail_categories_add_emits_remove(tmp_path):
    logger = _logger(tmp_path)
    _record_mutation(
        logger, op_id="op-5", cmd="mail-categories-add",
        drive_id="me", item_id="",
        args={"name": "Waiting", "color": "preset0"},
        before={},
        after={"id": "c-new", "display_name": "Waiting", "color": "preset0"},
    )
    rev = build_reverse_mail_operation(logger, "op-5")
    assert rev.action == "mail.categories.remove"
    assert rev.item_id == "c-new"


def test_reverse_mail_categories_update_emits_update_back(tmp_path):
    logger = _logger(tmp_path)
    _record_mutation(
        logger, op_id="op-6", cmd="mail-categories-update",
        drive_id="me", item_id="c1",
        args={"name": "Waiting-New", "color": "preset2"},
        before={"display_name": "Waiting", "color": "preset0"},
        after={"display_name": "Waiting-New", "color": "preset2"},
    )
    rev = build_reverse_mail_operation(logger, "op-6")
    assert rev.action == "mail.categories.update"
    assert rev.args == {"name": "Waiting", "color": "preset0"}


def test_reverse_mail_categories_remove_emits_add(tmp_path):
    logger = _logger(tmp_path)
    _record_mutation(
        logger, op_id="op-7", cmd="mail-categories-remove",
        drive_id="me", item_id="c1",
        args={},
        before={"display_name": "Waiting", "color": "preset0"},
        after=None,
    )
    rev = build_reverse_mail_operation(logger, "op-7")
    assert rev.action == "mail.categories.add"
    assert rev.args == {"name": "Waiting", "color": "preset0"}


# ---- failed-original rejection --------------------------------------------

def test_reverse_rejects_original_non_ok(tmp_path):
    logger = _logger(tmp_path)
    log_mutation_start(logger, op_id="op-bad", cmd="mail-folder-create",
                       args={"name": "X"}, drive_id="me", item_id="inbox", before={})
    log_mutation_end(logger, op_id="op-bad", after=None, result="error", error="conflict")
    with pytest.raises(Irreversible):
        build_reverse_mail_operation(logger, "op-bad")


# ---- Dispatcher registration ----------------------------------------------

def test_register_mail_inverses_registers_all_phase_2_verbs():
    d = Dispatcher()
    register_mail_inverses(d)
    # Reversible:
    for action in (
        "mail.folder.create", "mail.folder.rename", "mail.folder.move",
        "mail.categories.add", "mail.categories.update", "mail.categories.remove",
    ):
        assert d.is_registered(action), f"missing reversible registration for {action}"
    # Irreversible:
    assert d.is_registered("mail.folder.delete")
    with pytest.raises(IrreversibleOp):
        d.build_inverse("mail.folder.delete", before={}, after={})
```

- [ ] **Step 2:** `uv run pytest tests/test_mail_mutate_undo.py -q` → all FAIL.

- [ ] **Step 3: Implement `src/m365ctl/mail/mutate/undo.py`.**

```python
"""Build reverse-ops for mail mutations.

Mirror of ``onedrive.mutate.undo`` but scoped to the Phase 2 mail verbs:

    mail-folder-create      -> mail.folder.delete (on the new folder id)
    mail-folder-rename      -> mail.folder.rename back to before.display_name
    mail-folder-move        -> mail.folder.move back to before.parent_id
    mail-folder-delete      -> Irreversible (Phase 2 — folder restore is Phase 4+)
    mail-categories-add     -> mail.categories.remove on after.id
    mail-categories-update  -> mail.categories.update back to before
    mail-categories-remove  -> mail.categories.add from before.{display_name, color}
                               (NOTE: message→category links cannot be re-linked)
"""
from __future__ import annotations

from m365ctl.common.audit import AuditLogger, find_op_by_id
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.common.undo import Dispatcher
from m365ctl.onedrive.mutate.undo import Irreversible


def build_reverse_mail_operation(logger: AuditLogger, op_id: str) -> Operation:
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

    if cmd == "mail-folder-create":
        new_id = after.get("id")
        if not new_id:
            raise Irreversible(
                f"mail-folder-create op {op_id!r} has no recorded id in after; "
                f"cannot locate the folder to delete."
            )
        return Operation(
            op_id=new_op_id(), action="mail.folder.delete",
            drive_id=drive_id, item_id=new_id, args={},
            dry_run_result=f"(undo of {op_id}) delete created folder "
                           f"{after.get('path', new_id)!r}",
        )

    if cmd == "mail-folder-rename":
        prior = before.get("display_name")
        if not prior:
            raise Irreversible(
                f"rename op {op_id!r} has no before.display_name; cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.folder.rename",
            drive_id=drive_id, item_id=start["item_id"],
            args={"new_name": prior},
            dry_run_result=f"(undo of {op_id}) rename back to {prior!r}",
        )

    if cmd == "mail-folder-move":
        prior_parent = before.get("parent_id")
        if not prior_parent:
            raise Irreversible(
                f"move op {op_id!r} has no before.parent_id; cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.folder.move",
            drive_id=drive_id, item_id=start["item_id"],
            args={"destination_id": prior_parent,
                  "destination_path": before.get("path", "")},
            dry_run_result=f"(undo of {op_id}) move back to "
                           f"{before.get('path', prior_parent)!r}",
        )

    if cmd == "mail-folder-delete":
        raise Irreversible(
            f"op {op_id!r} deleted a mail folder — restoring folders from "
            f"Deleted Items requires manual intervention in Phase 2. "
            f"Folder restore lands Phase 4+."
        )

    if cmd == "mail-categories-add":
        new_id = after.get("id")
        if not new_id:
            raise Irreversible(
                f"categories-add op {op_id!r} has no after.id; cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.categories.remove",
            drive_id=drive_id, item_id=new_id, args={},
            dry_run_result=f"(undo of {op_id}) remove category "
                           f"{after.get('display_name', new_id)!r}",
        )

    if cmd == "mail-categories-update":
        args: dict = {}
        if "display_name" in before:
            args["name"] = before["display_name"]
        if "color" in before:
            args["color"] = before["color"]
        if not args:
            raise Irreversible(
                f"categories-update op {op_id!r} has empty before; cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.categories.update",
            drive_id=drive_id, item_id=start["item_id"],
            args=args,
            dry_run_result=f"(undo of {op_id}) update category back to "
                           f"{before.get('display_name', '?')!r}",
        )

    if cmd == "mail-categories-remove":
        name = before.get("display_name")
        if not name:
            raise Irreversible(
                f"categories-remove op {op_id!r} has no before.display_name; "
                f"cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.categories.add",
            drive_id=drive_id, item_id="",
            args={"name": name, "color": before.get("color", "preset0")},
            dry_run_result=f"(undo of {op_id}) re-add category {name!r} "
                           f"(message links cannot be restored)",
        )

    raise Irreversible(f"no reverse-op known for mail cmd {cmd!r}")


# ---- Dispatcher registration -----------------------------------------------

def _inverse_mail_folder_create(before: dict, after: dict) -> dict:
    return {"action": "mail.folder.delete", "args": {}}


def _inverse_mail_folder_rename(before: dict, after: dict) -> dict:
    return {"action": "mail.folder.rename",
            "args": {"new_name": before.get("display_name", "")}}


def _inverse_mail_folder_move(before: dict, after: dict) -> dict:
    return {"action": "mail.folder.move",
            "args": {"destination_id": before.get("parent_id", "")}}


def _inverse_mail_categories_add(before: dict, after: dict) -> dict:
    return {"action": "mail.categories.remove", "args": {}}


def _inverse_mail_categories_update(before: dict, after: dict) -> dict:
    args: dict = {}
    if "display_name" in before:
        args["name"] = before["display_name"]
    if "color" in before:
        args["color"] = before["color"]
    return {"action": "mail.categories.update", "args": args}


def _inverse_mail_categories_remove(before: dict, after: dict) -> dict:
    return {"action": "mail.categories.add",
            "args": {"name": before.get("display_name", ""),
                     "color": before.get("color", "preset0")}}


def register_mail_inverses(dispatcher: Dispatcher) -> None:
    """Register every Phase-2 mail inverse on ``dispatcher``."""
    dispatcher.register("mail.folder.create", _inverse_mail_folder_create)
    dispatcher.register("mail.folder.rename", _inverse_mail_folder_rename)
    dispatcher.register("mail.folder.move", _inverse_mail_folder_move)
    dispatcher.register("mail.categories.add", _inverse_mail_categories_add)
    dispatcher.register("mail.categories.update", _inverse_mail_categories_update)
    dispatcher.register("mail.categories.remove", _inverse_mail_categories_remove)
    dispatcher.register_irreversible(
        "mail.folder.delete",
        "Folder restore from Deleted Items requires manual intervention until Phase 4+.",
    )
```

- [ ] **Step 4: Wire into the top-level undo dispatcher.**

Open `src/m365ctl/cli/undo.py`. It currently delegates to `m365ctl.onedrive.cli.undo.main`. The OneDrive CLI already constructs a `Dispatcher` with `register_od_inverses`. For Phase 2, we need the SAME Dispatcher to also have `mail.*` inverses registered.

Find the `_DISPATCHER = Dispatcher()` / `register_od_inverses(_DISPATCHER)` block in `src/m365ctl/onedrive/cli/undo.py` (added in Group 6 of Phase 0). Add one line right after:
```python
from m365ctl.mail.mutate.undo import register_mail_inverses
register_mail_inverses(_DISPATCHER)
```

Also extend the cmd-dispatch switch. Currently it handles `od-*` cmds. For mail cmds, the switch should call `build_reverse_mail_operation(logger, op_id)` from `mail.mutate.undo`. The cleanest change: add a preflight branch that chooses the right builder based on the cmd prefix (`od-*` → existing builder, `mail-*` → new mail builder).

Find the `def run_undo(...)` function in `src/m365ctl/onedrive/cli/undo.py`. Near where `build_reverse_operation(logger, op_id)` is called, wrap it:
```python
from m365ctl.common.audit import find_op_by_id
from m365ctl.mail.mutate.undo import build_reverse_mail_operation
# (add these imports at module top, not inside the function)

# ... inside run_undo, where build_reverse_operation is called:
start, _ = find_op_by_id(logger, op_id)
cmd = (start or {}).get("cmd", "")
if cmd.startswith("mail-"):
    rev = build_reverse_mail_operation(logger, op_id)
else:
    rev = build_reverse_operation(logger, op_id)
```

If the code structure makes this preflight awkward, report BLOCKED with the specific file:line and suggested alternative (e.g. a factory function). For a clean refactor path, it's acceptable to:
1. Add `get_reverse_op_builder(cmd)` in `common/undo.py` that returns the right builder.
2. Call it from `onedrive.cli.undo.run_undo`.

Leave the choice to the implementer — the end-state behavior is what matters: `m365ctl undo <mail-op-id>` builds a mail reverse-op; `m365ctl undo <od-op-id>` builds an od reverse-op.

**NOTE:** the existing `onedrive.cli.undo` manual-switch executor (the `if rev.action == "rename": ...` chain) handles only od.* actions. For mail, we need the CLI to actually CALL the mail mutate functions too. Do this by adding a parallel switch: after `rev` is built, if `rev.action.startswith("mail.")`, dispatch to the appropriate `mail.mutate.*` execute function. Walk the code:

1. Read `src/m365ctl/onedrive/cli/undo.py` in full first.
2. Find where `rev.action` is switched.
3. Add `mail.folder.create/rename/move/delete` + `mail.categories.add/update/remove` branches.
4. Each branch builds the right `(graph, logger, before)` call. For mail ops, `graph` is a GraphClient built against the same credential as the ORIGINAL op (stored as `start["args"]["auth_mode"]`). If the original was delegated, use DelegatedCredential; else AppOnly.

If this is getting tangled, STOP and report BLOCKED. An alternative cleaner design: make the mail undo CLI a separate path — the top-level `m365ctl undo <op_id>` detects the cmd prefix and routes to one of two handlers (`onedrive.cli.undo.run_undo` or `mail.cli.undo.run_undo`). A new `src/m365ctl/mail/cli/undo.py` is small and mirrors the od shape.

- [ ] **Step 5: Tests**
```
uv run pytest tests/test_mail_mutate_undo.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
```
Expected: 386 + 10 (test_mail_mutate_undo tests) = 396.

- [ ] **Step 6: Commit.**
```bash
git add src/m365ctl/mail/mutate/undo.py src/m365ctl/onedrive/cli/undo.py src/m365ctl/cli/undo.py tests/test_mail_mutate_undo.py
git commit -m "feat(mail/mutate): undo — build_reverse_mail_operation + register_mail_inverses; wire top-level undo"
```

---

## Group 6: CLI — mail folders subcommands

### Task 6: Add `create/rename/move/delete` subcommands to `mail folders` CLI

**Files:**
- Modify: `src/m365ctl/mail/cli/folders.py`
- Create: `tests/test_cli_mail_folders_mutate.py`

The existing `src/m365ctl/mail/cli/folders.py` (from Phase 1) has ONLY a reader (no subparsers — it's `mail folders [--tree] [--with-counts] [--include-hidden]`). We need to add 4 subcommand verbs while keeping bare `mail folders` working as the reader.

Approach: add `subparsers` with `required=False`. When `args.subcommand is None` and no mutation flags, fall through to the existing list behavior. When `subcommand in ("create", "rename", "move", "delete")`, dispatch to the new handlers.

Design: the current file will grow substantially (~250 LOC). Keep it in one file for now; a future refactor can split reader vs mutator if it becomes unwieldy.

- [ ] **Step 1: Failing parser tests.**

Write `tests/test_cli_mail_folders_mutate.py`:
```python
"""Parser + scope-gate tests for `m365ctl mail folders {create,rename,move,delete}`."""
from __future__ import annotations

import pytest

from m365ctl.mail.cli.folders import build_parser


def test_folders_list_still_works_with_no_subcommand():
    args = build_parser().parse_args([])
    assert args.subcommand is None
    assert args.tree is False


def test_folders_list_still_works_with_tree_flag():
    args = build_parser().parse_args(["--tree"])
    assert args.subcommand is None
    assert args.tree is True


def test_folders_create_subparser():
    args = build_parser().parse_args(["create", "/Inbox", "Triage", "--confirm"])
    assert args.subcommand == "create"
    assert args.parent_path == "/Inbox"
    assert args.name == "Triage"
    assert args.confirm is True


def test_folders_create_requires_both_positional():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["create", "/Inbox"])


def test_folders_rename_subparser():
    args = build_parser().parse_args(["rename", "/Inbox/Triage", "Triaged", "--confirm"])
    assert args.subcommand == "rename"
    assert args.path == "/Inbox/Triage"
    assert args.new_name == "Triaged"


def test_folders_move_subparser():
    args = build_parser().parse_args(["move", "/Inbox/Triage", "/Archive", "--confirm"])
    assert args.subcommand == "move"
    assert args.path == "/Inbox/Triage"
    assert args.new_parent_path == "/Archive"


def test_folders_delete_subparser():
    args = build_parser().parse_args(["delete", "/Archive/Old", "--confirm"])
    assert args.subcommand == "delete"
    assert args.path == "/Archive/Old"
    assert args.confirm is True


def test_folders_mutations_without_confirm_is_dry_run():
    args = build_parser().parse_args(["create", "/Inbox", "X"])
    assert args.confirm is False


def test_folders_deny_folder_blocked_before_graph(tmp_path):
    """Attempting to create under Calendar/ (hardcoded deny) fails fast."""
    import pytest
    from m365ctl.common.safety import ScopeViolation
    from m365ctl.mail.cli.folders import main

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("""
tenant_id    = "00000000-0000-0000-0000-000000000000"
client_id    = "11111111-1111-1111-1111-111111111111"
cert_path    = "/tmp/nonexistent.key"
cert_public  = "/tmp/nonexistent.cer"
default_auth = "delegated"

[scope]
allow_drives    = ["me"]
allow_mailboxes = ["me"]

[catalog]
path = "cache/catalog.duckdb"

[mail]
catalog_path = "cache/mail.duckdb"

[logging]
ops_dir = "logs/ops"
""".lstrip())

    with pytest.raises(ScopeViolation):
        main(["--config", str(cfg_path), "create", "/Calendar", "Evil", "--confirm"])
```

- [ ] **Step 2: Implement the subcommand surface.**

Overwrite `src/m365ctl/mail/cli/folders.py` with the combined reader + mutator shape:
```python
"""`m365ctl mail folders [list|create|rename|move|delete]` — reader + CRUD."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.common.safety import is_folder_denied
from m365ctl.mail.cli._common import (
    add_common_args,
    emit_json_lines,
    load_and_authorize,
)
from m365ctl.mail.folders import FolderNotFound, list_folders, resolve_folder_path
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.folders import (
    execute_create_folder,
    execute_delete_folder,
    execute_move_folder,
    execute_rename_folder,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail folders")
    add_common_args(p)
    # Reader flags remain at the root for backwards compatibility with Phase 1.
    p.add_argument("--tree", action="store_true")
    p.add_argument("--with-counts", action="store_true")
    p.add_argument("--include-hidden", action="store_true")

    sub = p.add_subparsers(dest="subcommand", required=False)

    c = sub.add_parser("create", help="Create a child folder.")
    c.add_argument("parent_path", help="Parent folder path (use '' for root).")
    c.add_argument("name", help="New folder name.")
    c.add_argument("--confirm", action="store_true")

    r = sub.add_parser("rename", help="Rename a folder.")
    r.add_argument("path")
    r.add_argument("new_name")
    r.add_argument("--confirm", action="store_true")

    m = sub.add_parser("move", help="Move a folder under a new parent.")
    m.add_argument("path")
    m.add_argument("new_parent_path")
    m.add_argument("--confirm", action="store_true")

    d = sub.add_parser("delete", help="Delete a folder (soft delete).")
    d.add_argument("path")
    d.add_argument("--confirm", action="store_true")

    return p


# ---- reader (unchanged from Phase 1) --------------------------------------

def _run_list(args: argparse.Namespace) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    folders = list(list_folders(
        graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        include_hidden=args.include_hidden,
    ))
    folders = [f for f in folders if not is_folder_denied(f.path, cfg)]

    if args.json:
        emit_json_lines(folders)
        return 0

    if args.tree:
        for f in folders:
            depth = f.path.count("/")
            indent = "  " * depth
            counts = f"  ({f.total_items}/{f.unread_items})" if args.with_counts else ""
            print(f"{indent}{f.display_name}{counts}")
    else:
        for f in folders:
            counts = f"  ({f.total_items}/{f.unread_items})" if args.with_counts else ""
            print(f"{f.path}{counts}")
    return 0


# ---- mutations ------------------------------------------------------------

def _build_audit_logger(cfg) -> AuditLogger:
    return AuditLogger(ops_dir=cfg.logging.ops_dir)


def _require_confirm(args, verb: str) -> int:
    if args.confirm:
        return 0
    print(
        f"mail folders {verb}: dry-run (use --confirm to execute)",
        file=sys.stderr,
    )
    return 0


def _run_create(args: argparse.Namespace) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    # New folder path = parent_path + "/" + name; denial check covers both parent
    # and future-child compliance rejects.
    new_path = f"{args.parent_path.rstrip('/')}/{args.name}" if args.parent_path else args.name
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope, folder_path=new_path,
    )
    # Dry-run short-circuit: no Graph call.
    if not args.confirm:
        print(f"(dry-run) would create folder {new_path!r}", file=sys.stderr)
        return 0

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    if args.parent_path:
        parent_id = resolve_folder_path(
            args.parent_path, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
    else:
        parent_id = ""

    op = Operation(
        op_id=new_op_id(),
        action="mail.folder.create",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id=parent_id,
        args={"name": args.name, "parent_path": args.parent_path, "auth_mode": auth_mode},
    )
    result = execute_create_folder(op, graph, _build_audit_logger(cfg), before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — created {new_path!r} (id: {(result.after or {}).get('id', '')})")
    return 0


def _run_rename(args: argparse.Namespace) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope, folder_path=args.path,
    )
    if not args.confirm:
        print(f"(dry-run) would rename {args.path!r} -> {args.new_name!r}", file=sys.stderr)
        return 0

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        folder_id = resolve_folder_path(
            args.path, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
    except FolderNotFound as e:
        print(f"mail folders rename: {e}", file=sys.stderr)
        return 2

    op = Operation(
        op_id=new_op_id(),
        action="mail.folder.rename",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id=folder_id,
        args={"new_name": args.new_name, "auth_mode": auth_mode},
    )
    # Capture before = current display_name (last segment of path).
    before = {"display_name": args.path.strip("/").split("/")[-1], "path": args.path}
    result = execute_rename_folder(op, graph, _build_audit_logger(cfg), before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — renamed {args.path!r} -> {args.new_name!r}")
    return 0


def _run_move(args: argparse.Namespace) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    # BOTH source and destination must pass the deny-folder check.
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope, folder_path=args.path,
    )
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope, folder_path=args.new_parent_path,
    )
    if not args.confirm:
        print(
            f"(dry-run) would move {args.path!r} -> {args.new_parent_path!r}",
            file=sys.stderr,
        )
        return 0

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        folder_id = resolve_folder_path(
            args.path, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
        dest_id = resolve_folder_path(
            args.new_parent_path, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
    except FolderNotFound as e:
        print(f"mail folders move: {e}", file=sys.stderr)
        return 2

    # Before: capture parent_id for undo. Compute from the source path.
    parent_path = "/".join(args.path.strip("/").split("/")[:-1])
    parent_id = ""
    if parent_path:
        try:
            parent_id = resolve_folder_path(
                parent_path, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
            )
        except FolderNotFound:
            parent_id = ""

    op = Operation(
        op_id=new_op_id(),
        action="mail.folder.move",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id=folder_id,
        args={"destination_id": dest_id, "destination_path": args.new_parent_path,
              "auth_mode": auth_mode},
    )
    before = {"parent_id": parent_id, "path": args.path}
    result = execute_move_folder(op, graph, _build_audit_logger(cfg), before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — moved {args.path!r} -> {args.new_parent_path!r}")
    return 0


def _run_delete(args: argparse.Namespace) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope, folder_path=args.path,
    )
    if not args.confirm:
        print(f"(dry-run) would delete folder {args.path!r}", file=sys.stderr)
        return 0

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        folder_id = resolve_folder_path(
            args.path, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
    except FolderNotFound as e:
        print(f"mail folders delete: {e}", file=sys.stderr)
        return 2

    op = Operation(
        op_id=new_op_id(),
        action="mail.folder.delete",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id=folder_id,
        args={"auth_mode": auth_mode},
    )
    before = {"id": folder_id, "display_name": args.path.strip("/").split("/")[-1],
              "path": args.path}
    result = execute_delete_folder(op, graph, _build_audit_logger(cfg), before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — deleted folder {args.path!r}")
    return 0


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.subcommand is None:
        return _run_list(args)
    if args.subcommand == "create":
        return _run_create(args)
    if args.subcommand == "rename":
        return _run_rename(args)
    if args.subcommand == "move":
        return _run_move(args)
    if args.subcommand == "delete":
        return _run_delete(args)
    return 2
```

- [ ] **Step 3: Run tests.**
```bash
uv run pytest tests/test_cli_mail_folders.py tests/test_cli_mail_folders_mutate.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
```
Expected: the old `tests/test_cli_mail_folders.py` still passes (Phase 1's parser tests still work because we kept the same flags at the root level); new tests all pass; total ~ 386 + 9 new = 395.

- [ ] **Step 4: Commit.**
```bash
git add src/m365ctl/mail/cli/folders.py tests/test_cli_mail_folders_mutate.py
git commit -m "feat(mail/cli): mail-folders subcommands — create/rename/move/delete with --confirm gate"
```

---

## Group 7: CLI — mail categories subcommands

### Task 7: Add `add/update/remove/sync` subcommands to `mail categories`

**Files:**
- Modify: `src/m365ctl/mail/cli/categories.py`
- Create: `tests/test_cli_mail_categories_mutate.py`

- [ ] **Step 1: Failing tests.**

Write `tests/test_cli_mail_categories_mutate.py`:
```python
import pytest

from m365ctl.mail.cli.categories import build_parser


def test_categories_list_still_works_with_no_subcommand():
    args = build_parser().parse_args([])
    assert args.subcommand is None


def test_categories_list_subcommand_still_works():
    args = build_parser().parse_args(["list"])
    assert args.subcommand == "list"


def test_categories_add_subparser():
    args = build_parser().parse_args(["add", "Followup", "--color", "preset0", "--confirm"])
    assert args.subcommand == "add"
    assert args.name == "Followup"
    assert args.color == "preset0"
    assert args.confirm is True


def test_categories_add_default_color():
    args = build_parser().parse_args(["add", "X"])
    assert args.color == "preset0"


def test_categories_update_subparser():
    args = build_parser().parse_args(["update", "cat-id", "--name", "New", "--color", "preset2", "--confirm"])
    assert args.subcommand == "update"
    assert args.id == "cat-id"
    assert args.name == "New"
    assert args.color == "preset2"


def test_categories_remove_subparser():
    args = build_parser().parse_args(["remove", "cat-id", "--confirm"])
    assert args.subcommand == "remove"
    assert args.id == "cat-id"


def test_categories_sync_subparser():
    args = build_parser().parse_args(["sync", "--confirm"])
    assert args.subcommand == "sync"
```

- [ ] **Step 2: Implement.**

Overwrite `src/m365ctl/mail/cli/categories.py`:
```python
"""`m365ctl mail categories [list|add|update|remove|sync]` — reader + CRUD."""
from __future__ import annotations

import argparse
import sys

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.categories import list_master_categories
from m365ctl.mail.cli._common import (
    add_common_args,
    emit_json_lines,
    load_and_authorize,
)
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.categories import (
    compute_sync_plan,
    execute_add_category,
    execute_remove_category,
    execute_update_category,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail categories")
    add_common_args(p)
    sub = p.add_subparsers(dest="subcommand", required=False)

    sub.add_parser("list", help="List master categories (default).")

    a = sub.add_parser("add", help="Add a master category.")
    a.add_argument("name")
    a.add_argument("--color", default="preset0", help="preset0..preset24 (default: preset0)")
    a.add_argument("--confirm", action="store_true")

    u = sub.add_parser("update", help="Update a master category.")
    u.add_argument("id")
    u.add_argument("--name", help="New display name.")
    u.add_argument("--color", help="New color (preset0..preset24).")
    u.add_argument("--confirm", action="store_true")

    rm = sub.add_parser("remove", help="Remove a master category.")
    rm.add_argument("id")
    rm.add_argument("--confirm", action="store_true")

    s = sub.add_parser("sync", help="Reconcile categories_master from config.")
    s.add_argument("--confirm", action="store_true")
    return p


def _build_logger(cfg) -> AuditLogger:
    return AuditLogger(ops_dir=cfg.logging.ops_dir)


def _run_list(args) -> int:
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    cats = list_master_categories(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)

    if args.json:
        emit_json_lines(cats)
    else:
        for c in cats:
            print(f"{c.color:<12}  {c.display_name}  (id: {c.id})")
    return 0


def _run_add(args) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        print(f"(dry-run) would add category {args.name!r} color={args.color!r}", file=sys.stderr)
        return 0
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    op = Operation(
        op_id=new_op_id(), action="mail.categories.add",
        drive_id=derive_mailbox_upn(args.mailbox), item_id="",
        args={"name": args.name, "color": args.color, "auth_mode": auth_mode},
    )
    result = execute_add_category(op, graph, _build_logger(cfg), before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — added {args.name!r}")
    return 0


def _run_update(args) -> int:
    if args.name is None and args.color is None:
        print("mail categories update: pass --name or --color (or both)", file=sys.stderr)
        return 2
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        patch = {}
        if args.name is not None:
            patch["name"] = args.name
        if args.color is not None:
            patch["color"] = args.color
        print(f"(dry-run) would update category {args.id} with {patch}", file=sys.stderr)
        return 0
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    # Fetch current state for `before` — we need display_name + color to undo.
    current = list_master_categories(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)
    before = next(
        ({"display_name": c.display_name, "color": c.color} for c in current if c.id == args.id),
        {},
    )
    call_args: dict = {"auth_mode": auth_mode}
    if args.name is not None:
        call_args["name"] = args.name
    if args.color is not None:
        call_args["color"] = args.color
    op = Operation(
        op_id=new_op_id(), action="mail.categories.update",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.id,
        args=call_args,
    )
    result = execute_update_category(op, graph, _build_logger(cfg), before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — updated {args.id}")
    return 0


def _run_remove(args) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        print(f"(dry-run) would remove category {args.id}", file=sys.stderr)
        return 0
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    current = list_master_categories(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)
    before = next(
        ({"display_name": c.display_name, "color": c.color} for c in current if c.id == args.id),
        {},
    )
    op = Operation(
        op_id=new_op_id(), action="mail.categories.remove",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.id,
        args={"auth_mode": auth_mode},
    )
    result = execute_remove_category(op, graph, _build_logger(cfg), before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — removed {args.id}")
    return 0


def _run_sync(args) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    live = list_master_categories(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)
    desired = list(cfg.mail.categories_master)
    plan = compute_sync_plan(live, desired)
    if not plan:
        print("mail categories sync: already in sync — nothing to do.")
        return 0
    if not args.confirm:
        for op_spec in plan:
            print(f"(dry-run) would add category {op_spec['args']['name']!r}")
        print(f"(dry-run) {len(plan)} categories to add (use --confirm to execute).",
              file=sys.stderr)
        return 0
    logger = _build_logger(cfg)
    any_error = False
    for op_spec in plan:
        op_spec["args"]["auth_mode"] = auth_mode
        op = Operation(**op_spec)
        result = execute_add_category(op, graph, logger, before={})
        if result.status != "ok":
            any_error = True
            print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
        else:
            print(f"[{op.op_id}] ok — added {op.args['name']!r}")
    return 1 if any_error else 0


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.subcommand in (None, "list"):
        return _run_list(args)
    if args.subcommand == "add":
        return _run_add(args)
    if args.subcommand == "update":
        return _run_update(args)
    if args.subcommand == "remove":
        return _run_remove(args)
    if args.subcommand == "sync":
        return _run_sync(args)
    return 2
```

- [ ] **Step 3: Run tests + commit.**
```bash
uv run pytest tests/test_cli_mail_categories.py tests/test_cli_mail_categories_mutate.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/cli/categories.py tests/test_cli_mail_categories_mutate.py
git commit -m "feat(mail/cli): mail-categories subcommands — add/update/remove/sync with --confirm gate"
```

Expected suite: 395 + 7 = 402.

---

## Group 8: Version bump + CHANGELOG + final gates

### Task 8: Bump 0.3.0 + CHANGELOG + push + merge

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Bump version.**

`pyproject.toml`: `version = "0.2.0"` → `version = "0.3.0"`.

- [ ] **Step 2: Add CHANGELOG entry above `[0.2.0]`:**

```markdown
## [0.3.0] — 2026-04-24

### Added
- **Mail folder CRUD:** `m365ctl mail folders create/rename/move/delete` (soft delete).
  Dry-run default; `--confirm` to execute. Plan-file bulk workflow via `--from-plan` stays the shape of OneDrive mutations.
- **Master-category CRUD + sync:** `m365ctl mail categories add/update/remove/sync`. `sync` reconciles from `[mail].categories_master` — only adds missing; never removes user-created extras.
- **Mail undo:** `m365ctl undo <op-id>` now handles `mail.folder.*` and `mail.categories.*` ops alongside existing `od.*` ops.
  - `mail.folder.create` ↔ `mail.folder.delete`
  - `mail.folder.rename` ↔ rename back
  - `mail.folder.move` ↔ move back
  - `mail.folder.delete` — **Irreversible in Phase 2** (folder restore from Deleted Items lands Phase 4+)
  - `mail.categories.add` ↔ `mail.categories.remove`
  - `mail.categories.update` ↔ update back
  - `mail.categories.remove` ↔ `mail.categories.add` (message→category links cannot be restored)
- `src/m365ctl/mail/mutate/` tree: `folders.py`, `categories.py`, `undo.py`, `_common.py` (`MailResult`, `assert_mail_target_allowed`, `derive_mailbox_upn`).
- Plan-file schema accepts `mail.folder.*` + `mail.categories.*` action namespaces.

### Changed
- `src/m365ctl/mail/cli/folders.py` gains subcommands while preserving Phase 1 bare-invocation reader behavior.
- `src/m365ctl/mail/cli/categories.py` gains subcommands while preserving bare-invocation list behavior.

### Safety
- All mail mutations go through `assert_mail_target_allowed` — mailbox scope + hardcoded compliance folder deny list (`Recoverable Items`, `Purges`, `Audits`, `Calendar`, `Contacts`, `Tasks`, `Notes`) enforced before any Graph call.
- `--confirm` required for every mutation. Dry-run is default.
```

- [ ] **Step 3: Commit the release bump.**
```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore(release): bump to 0.3.0 + CHANGELOG entry for mail folder + category CRUD"
```

### Task 9: Acceptance gates

- [ ] **Step 1:** `uv run pytest -m "not live" -q 2>&1 | tail -3` → 402 passed, 1 deselected (or similar; verify baseline + Phase 2 additions).
- [ ] **Step 2:** `uv run ruff check 2>&1 | tail -5` → clean (if not, fix in a `fix(lint): ...` commit).
- [ ] **Step 3:** `uv run mypy src 2>&1 | tail -10` → report count (Phase 0 baseline 31 → Phase 1 added 12 → 43). If it grew by > 10 new mail errors, triage — flag each.
- [ ] **Step 4: CLI smoke.**
```bash
uv run python -m m365ctl mail folders --help
uv run python -m m365ctl mail folders create --help
uv run python -m m365ctl mail folders rename --help
uv run python -m m365ctl mail folders move --help
uv run python -m m365ctl mail folders delete --help
uv run python -m m365ctl mail categories --help
uv run python -m m365ctl mail categories add --help
uv run python -m m365ctl mail categories update --help
uv run python -m m365ctl mail categories remove --help
uv run python -m m365ctl mail categories sync --help
uv run python -m m365ctl undo --help
```
All exit 0.

- [ ] **Step 5: Commit the plan file.**
```bash
git add docs/superpowers/plans/2026-04-24-phase-2-mail-folder-category-crud.md
git commit -m "docs(plans): commit Phase 2 mail folder + category CRUD plan"
```

### Task 10: Push + PR + merge

- [ ] **Step 1:** `git push -u origin phase-2-mail-folder-category-crud`.
- [ ] **Step 2:** `gh pr create` with a summary of Phase 2 + test plan checklist.
- [ ] **Step 3:** `gh pr checks <N> --watch` → all 6 matrix cells green.
- [ ] **Step 4:** `gh pr merge <N> --merge --delete-branch`.
- [ ] **Step 5:** `git checkout main && git pull`.

### User-performed live-tenant smoke (after merge)

Give the user this list; they run it on their real mailbox:
```bash
./bin/mail-folders create /Inbox Phase2Test --confirm
./bin/mail-folders rename /Inbox/Phase2Test PhaseTwoTest --confirm
./bin/mail-folders move /Inbox/PhaseTwoTest /Archive --confirm
./bin/m365ctl-undo <last-op-id>        # should reverse the move
./bin/m365ctl-undo <rename-op-id>
./bin/mail-folders delete /Inbox/PhaseTwoTest --confirm  # soft delete
./bin/mail-categories add TestCat --color preset5 --confirm
./bin/mail-categories remove <id> --confirm
```

---

## Self-review

**1. Spec coverage (§19 Phase 2 deliverables):**
- [x] `mail.mutate.folders` with before/after capture → Tasks 3 + 6.
- [x] `mail.mutate.categories` including sync → Tasks 4 + 7.
- [x] `_VALID_ACTIONS` extended with `mail.folder.*` + `mail.categories.*` → Task 1.
- [x] Dispatcher inverses registered → Task 5.
- [x] CLI subcommands for `mail folders` and `mail categories` → Tasks 6 + 7.
- [x] Tests (unit, mocked) → every Task has them; live smoke documented as user-performed.
- [x] Bump to 0.3.0 → Task 8.

**2. Acceptance (§19 Phase 2):**
- `mail-folders create Inbox Triage --confirm` → Task 6 `_run_create`.
- `mail-folders rename ... --confirm` + `m365ctl undo` → Tasks 6 + 5.
- `mail-categories sync --confirm` → Task 7 `_run_sync`.
- Plan-file bulk workflow — Phase 2 doesn't ship a `--pattern` driven emitter; `--from-plan` reads ops from an existing plan JSON. Plan authoring from pattern is Phase 3+ (message mutations). Sufficient for Phase 2 acceptance because folder CRUD is low-volume by nature.
- Hardcoded deny-folder test — Task 6 `test_folders_deny_folder_blocked_before_graph`.

**3. Placeholder scan:** No TODOs. `mail.folder.delete` irreversibility is EXPLICITLY documented in Dispatcher registration + CHANGELOG.

**4. Type consistency:**
- `MailResult(op_id, status, error, after)` — consistent across all `execute_*` returns.
- `execute_*(op, graph, logger, *, before)` — consistent signature across all 8 mutation executors.
- `op.drive_id` = mailbox UPN (or "me"), `op.item_id` = target folder/category id, `op.args["auth_mode"]` = "delegated"/"app-only" — consistent throughout.
- `derive_mailbox_upn(spec)` defined once in `_common.py`, consumed by CLI layer.
- `assert_mail_target_allowed(cfg, *, mailbox_spec, auth_mode, unsafe_scope, folder_path=None)` — consistent across both CLI files.

---

Plan complete.
