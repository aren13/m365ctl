# Phase 4 — Mail Soft-Delete + Restore (0.5.0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `m365ctl mail delete` — the first verb that actually removes a message from its folder. Uses Graph's "move to Deleted Items" semantics (a soft delete; nothing is permanently purged). Every delete is undoable via `m365ctl undo`, which moves the message back to its original parent. Closes the Phase 3 loose end where `mail.copy`'s inverse (`mail.delete.soft`) pointed at a Phase 4 placeholder. Bumps version to 0.5.0.

**Architecture:**
- One new executor `src/m365ctl/mail/mutate/delete.py` (`execute_soft_delete`). Under the hood, it's a `POST /messages/{id}/move` with `destinationId="deleteditems"` — the Graph well-known folder alias for the mailbox's Deleted Items folder. Captures `before.{parent_folder_id, parent_folder_path}` so undo can put it back.
- Undo: the Dispatcher's existing `mail.delete.soft` entry (registered in Phase 3 as a placeholder) is replaced with a real inverse that emits `mail.move` back to `before.parent_folder_id`. `build_reverse_mail_operation` gains a `cmd == "mail-delete-soft"` branch that produces a concrete `mail.move` `Operation`. The CLI's `mail.delete.soft` dispatch branch (currently returns exit 2 with "deferred to Phase 4" stderr) is replaced with a real `execute_soft_delete` call.
- This also closes the Phase 3 `mail.copy` undo chain: `mail.copy → mail.delete.soft` now resolves all the way through to a working executor.
- CLI `src/m365ctl/mail/cli/delete.py` mirrors the Phase 3 `move.py` shape: single-item (`--message-id`), bulk plan-out (filter flags + `--plan-out`), bulk execute (`--from-plan --confirm`). `--help` carries an explicit "for hard delete see `mail-clean` (Phase 6)" note.
- `bin/mail-delete` wrapper; dispatcher route for `mail delete` verb.

**Tech Stack:** Python 3.11+ stdlib, httpx, msal. No new dependencies. Reuses `MessageFilter`, `expand_messages_for_pattern`, `emit_plan`, `confirm_bulk_proceed` from `mail/cli/_bulk.py`.

**Parent spec:** `docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md` — §9.2 (delete maps to move-to-Deleted-Items), §10.3 (CLI shape), §12.1 (before/after capture + inverse), §19 Phase 4 (deliverables + acceptance).

**Safety posture:**
- **`--confirm` required.** Dry-run is the default. Bulk ≥20 ops → interactive `/dev/tty` confirm (non-bypassable) via `confirm_bulk_proceed`.
- **`assert_mail_target_allowed` runs BEFORE credential construction.** Source-folder deny check + mailbox allow-list gate.
- **Soft delete is reversible** until the user hard-purges the Deleted Items folder (out of scope — Phase 6). If the message was manually purged between delete and undo, the undo returns a clean "not found" error with guidance.
- No force-push; no `git add -A`. Feature branch `phase-4-mail-soft-delete-restore` off `main`.

---

## File Structure (Phase 4 target)

```
m365ctl/
├── pyproject.toml                                   # MODIFIED — version 0.5.0
├── CHANGELOG.md                                     # MODIFIED — [0.5.0] entry
├── bin/
│   └── mail-delete                                  # NEW
├── src/m365ctl/
│   └── mail/
│       ├── cli/
│       │   ├── __main__.py                          # MODIFIED — add `delete` verb route
│       │   ├── delete.py                            # NEW
│       │   └── undo.py                              # MODIFIED — mail.delete.soft dispatch now calls real executor
│       └── mutate/
│           ├── delete.py                            # NEW — execute_soft_delete
│           └── undo.py                              # MODIFIED — mail-delete-soft reverse branch + real Dispatcher inverse
└── tests/
    ├── test_mail_mutate_delete.py                   # NEW
    ├── test_mail_mutate_undo_phase4.py              # NEW — inverses for mail-delete-soft + closed mail.copy chain
    └── test_cli_mail_delete.py                      # NEW
```

---

## Preflight

### Task 0: Branch + baseline

- [ ] **Step 1:** `git status` → clean. `git branch --show-current` → `main`.
- [ ] **Step 2:** `git checkout -b phase-4-mail-soft-delete-restore`
- [ ] **Step 3:** `uv run pytest -m "not live" -q 2>&1 | tail -3` → expect **466 passed, 1 deselected**.

---

## Group 1: soft-delete executor

### Task 1: `mail/mutate/delete.py` — `execute_soft_delete`

**Files:**
- Create: `src/m365ctl/mail/mutate/delete.py`
- Create: `tests/test_mail_mutate_delete.py`

- [ ] **Step 1: Failing tests.**

Write `tests/test_mail_mutate_delete.py`:
```python
"""Tests for m365ctl.mail.mutate.delete — soft delete via move-to-Deleted-Items."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.delete import execute_soft_delete


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_soft_delete_moves_to_deleteditems_well_known(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "m1", "parentFolderId": "deleteditems-id"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-del",
        action="mail.delete.soft",
        drive_id="me",
        item_id="m1",
        args={},
    )
    result = execute_soft_delete(
        op, graph, logger,
        before={"parent_folder_id": "inbox", "parent_folder_path": "/Inbox"},
    )
    assert result.status == "ok"
    assert result.after == {"parent_folder_id": "deleteditems-id",
                            "deleted_from": "inbox"}
    assert graph.post.call_args.args[0] == "/me/messages/m1/move"
    assert graph.post.call_args.kwargs["json"] == {"destinationId": "deleteditems"}
    entries = list(iter_audit_entries(logger))
    assert [e["phase"] for e in entries] == ["start", "end"]
    assert entries[0]["cmd"] == "mail-delete-soft"
    assert entries[0]["before"]["parent_folder_id"] == "inbox"
    assert entries[1]["after"]["parent_folder_id"] == "deleteditems-id"


def test_soft_delete_app_only_routes_via_users_upn(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "m1", "parentFolderId": "deleteditems-id"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-ao",
        action="mail.delete.soft",
        drive_id="bob@example.com",
        item_id="m1",
        args={"auth_mode": "app-only"},
    )
    execute_soft_delete(op, graph, logger, before={})
    assert graph.post.call_args.args[0] == "/users/bob@example.com/messages/m1/move"


def test_soft_delete_graph_error(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.post.side_effect = GraphError("not found")
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-err",
        action="mail.delete.soft",
        drive_id="me", item_id="m1",
        args={},
    )
    result = execute_soft_delete(op, graph, logger, before={})
    assert result.status == "error"
    assert "not found" in (result.error or "")


def test_soft_delete_captures_empty_before_gracefully(tmp_path):
    """If the caller can't fetch the source folder (e.g. Graph 404 on get_message),
    empty before is acceptable — the soft-delete still works; undo may be best-effort."""
    graph = MagicMock()
    graph.post.return_value = {"id": "m1", "parentFolderId": "deleteditems-id"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-empty",
        action="mail.delete.soft",
        drive_id="me", item_id="m1",
        args={},
    )
    result = execute_soft_delete(op, graph, logger, before={})
    assert result.status == "ok"
    # deleted_from is empty when before has no parent_folder_id
    assert result.after == {"parent_folder_id": "deleteditems-id", "deleted_from": ""}
```

Run: `uv run pytest tests/test_mail_mutate_delete.py -q` → 4 FAIL.

- [ ] **Step 2: Implement `src/m365ctl/mail/mutate/delete.py`.**

```python
"""Soft delete — move message to the Deleted Items folder.

Graph's convention: the literal string ``"deleteditems"`` is the well-known
folder alias. POSTing to ``/messages/{id}/move`` with ``destinationId="deleteditems"``
moves the message into the mailbox's Deleted Items folder. Nothing is
permanently removed — a later ``m365ctl undo`` can move it back.

Hard delete (``mail-clean``) — a separate verb arriving Phase 6 — uses
``DELETE /messages/{id}`` which bypasses Deleted Items. Do not confuse the two.
"""
from __future__ import annotations

from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.mutate._common import MailResult


def _user_base(op: Operation) -> str:
    auth_mode = op.args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)


def execute_soft_delete(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /messages/{id}/move {destinationId: "deleteditems"}.

    ``before`` must contain ``parent_folder_id`` + ``parent_folder_path`` for
    undo to place the message back. The CLI layer fetches these via
    ``get_message`` before calling. If the pre-fetch fails, empty ``before``
    is acceptable — the delete still succeeds; undo degrades to best-effort.
    """
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-delete-soft",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        response = graph.post(
            f"{ub}/messages/{op.item_id}/move",
            json={"destinationId": "deleteditems"},
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {
        "parent_folder_id": response.get("parentFolderId", ""),
        "deleted_from": before.get("parent_folder_id", ""),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
```

- [ ] **Step 3: Run + commit.**

```bash
uv run pytest tests/test_mail_mutate_delete.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/mutate/delete.py tests/test_mail_mutate_delete.py
git commit -m "feat(mail/mutate): soft delete executor — POST /messages/{id}/move to deleteditems"
```

Expected suite: 466 + 4 = 470.

---

## Group 2: Undo — real inverse + close `mail.copy` chain

### Task 2: Extend undo builders + replace Phase 3 placeholder

**Files:**
- Modify: `src/m365ctl/mail/mutate/undo.py` — add `cmd == "mail-delete-soft"` branch to `build_reverse_mail_operation`; replace the placeholder lambda in `register_mail_inverses` with a real inverse.
- Modify: `src/m365ctl/mail/cli/undo.py` — replace the `action == "mail.delete.soft"` branch (currently prints "deferred to Phase 4") with a real `execute_soft_delete` call.
- Create: `tests/test_mail_mutate_undo_phase4.py`

- [ ] **Step 1: Failing tests.**

Write `tests/test_mail_mutate_undo_phase4.py`:
```python
"""Reverse-op tests for mail-delete-soft + closed mail.copy chain."""
from __future__ import annotations

from pathlib import Path

import pytest

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.undo import Dispatcher
from m365ctl.mail.mutate.undo import (
    build_reverse_mail_operation,
    register_mail_inverses,
)
from m365ctl.onedrive.mutate.undo import Irreversible


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def _record(logger, *, op_id, cmd, drive_id, item_id, args, before, after):
    log_mutation_start(logger, op_id=op_id, cmd=cmd, args=args,
                       drive_id=drive_id, item_id=item_id, before=before)
    log_mutation_end(logger, op_id=op_id, after=after, result="ok")


def test_reverse_mail_delete_soft_emits_move_back(tmp_path):
    """Undo of mail-delete-soft = move back to before.parent_folder_id."""
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-del", cmd="mail-delete-soft",
        drive_id="me", item_id="m1",
        args={},
        before={"parent_folder_id": "inbox", "parent_folder_path": "/Inbox"},
        after={"parent_folder_id": "deleteditems-id", "deleted_from": "inbox"},
    )
    rev = build_reverse_mail_operation(logger, "op-del")
    assert rev.action == "mail.move"
    assert rev.drive_id == "me"
    assert rev.item_id == "m1"
    assert rev.args["destination_id"] == "inbox"
    assert rev.args.get("destination_path") == "/Inbox"


def test_reverse_mail_delete_soft_rejects_missing_before_parent(tmp_path):
    """If before captures no parent_folder_id, we can't know where to restore to."""
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-bad", cmd="mail-delete-soft",
        drive_id="me", item_id="m1",
        args={},
        before={},
        after={"parent_folder_id": "deleteditems-id"},
    )
    with pytest.raises(Irreversible):
        build_reverse_mail_operation(logger, "op-bad")


def test_dispatcher_mail_delete_soft_inverse_returns_move_back():
    """Dispatcher's inverse for mail.delete.soft is now a real (before, after) -> move-back spec."""
    d = Dispatcher()
    register_mail_inverses(d)
    inv = d.build_inverse(
        "mail.delete.soft",
        before={"parent_folder_id": "inbox", "parent_folder_path": "/Inbox"},
        after={"parent_folder_id": "deleteditems-id"},
    )
    assert inv["action"] == "mail.move"
    assert inv["args"]["destination_id"] == "inbox"


def test_dispatcher_mail_copy_inverse_chains_to_delete_soft_then_move():
    """mail.copy inverse → mail.delete.soft. The chain works end-to-end via build_reverse."""
    # Confirms Phase 3's copy reversal step still yields mail.delete.soft (unchanged);
    # Phase 4's job is making mail.delete.soft EXECUTABLE, not changing the builder.
    d = Dispatcher()
    register_mail_inverses(d)
    inv = d.build_inverse("mail.copy", before={}, after={"new_message_id": "m1-copy"})
    assert inv["action"] == "mail.delete.soft"
```

Run: `uv run pytest tests/test_mail_mutate_undo_phase4.py -q` → 4 FAIL.

- [ ] **Step 2: Extend `build_reverse_mail_operation` in `src/m365ctl/mail/mutate/undo.py`.**

Find the existing `if cmd == "mail-categorize":` branch (added in Phase 3 G6). AFTER that branch, BEFORE the final `raise Irreversible(...)`, add the new one:

```python
    if cmd == "mail-delete-soft":
        prior_parent = before.get("parent_folder_id")
        if not prior_parent:
            raise Irreversible(
                f"mail-delete-soft op {op_id!r} has no before.parent_folder_id; "
                f"cannot determine where to restore to. "
                f"(If the message was already in Deleted Items when deleted, "
                f"the original folder is unrecoverable.)"
            )
        return Operation(
            op_id=new_op_id(), action="mail.move",
            drive_id=drive_id, item_id=start["item_id"],
            args={"destination_id": prior_parent,
                  "destination_path": before.get("parent_folder_path", "")},
            dry_run_result=f"(undo of {op_id}) restore {start['item_id']!r} "
                           f"to {before.get('parent_folder_path', prior_parent)!r}",
        )
```

- [ ] **Step 3: Replace the placeholder Dispatcher lambda in `register_mail_inverses`.**

Find the current entry:
```python
    dispatcher.register("mail.delete.soft", lambda b, a: {
        "action": "mail.delete.soft", "args": {},
    })
```

Replace with a real inverse:
```python
    dispatcher.register("mail.delete.soft", lambda b, a: {
        "action": "mail.move",
        "args": {"destination_id": b.get("parent_folder_id", ""),
                 "destination_path": b.get("parent_folder_path", "")},
    })
```

- [ ] **Step 4: Replace the Phase 3 placeholder branch in `src/m365ctl/mail/cli/undo.py`.**

Find the `elif action == "mail.delete.soft":` branch in `run_undo_mail`. Its current body prints a "Phase 4 TBD" stderr message and returns 2. Replace with:

```python
    elif action == "mail.delete.soft":
        from m365ctl.mail.mutate.delete import execute_soft_delete
        rev.args.setdefault("auth_mode", auth_mode)
        # before capture for the undo of THIS undo (i.e. if user later re-deletes the
        # restored message): fetch current parent so the next audit record is useful.
        try:
            from m365ctl.mail.messages import get_message
            msg = get_message(
                graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
                message_id=rev.item_id,
            )
            current_before = {
                "parent_folder_id": msg.parent_folder_id,
                "parent_folder_path": msg.parent_folder_path,
            }
        except Exception:
            current_before = {}
        r = execute_soft_delete(rev, graph, logger, before=current_before)
```

- [ ] **Step 5: Run + commit.**

```bash
uv run pytest tests/test_mail_mutate_undo_phase4.py tests/test_mail_mutate_undo_phase3.py tests/test_mail_mutate_undo.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/mutate/undo.py src/m365ctl/mail/cli/undo.py tests/test_mail_mutate_undo_phase4.py
git commit -m "feat(mail/mutate): undo of soft-delete restores to prior parent; close mail.copy undo chain"
```

Expected suite: 470 + 4 = 474.

---

## Group 3: CLI + bin wrapper + dispatcher route

### Task 3: `mail/cli/delete.py` + routing + bin wrapper

**Files:**
- Create: `src/m365ctl/mail/cli/delete.py`
- Modify: `src/m365ctl/mail/cli/__main__.py` — add `delete` route; update `_USAGE` listing.
- Create: `bin/mail-delete`
- Create: `tests/test_cli_mail_delete.py`

Three modes mirror Phase 3's `move.py`:
1. Single-item: `--message-id <id> --confirm`
2. Bulk dry-run: filter flags + `--plan-out <file>`
3. Bulk execute: `--from-plan <file> --confirm`

- [ ] **Step 1: Failing parser tests.**

Write `tests/test_cli_mail_delete.py`:
```python
import pytest

from m365ctl.mail.cli.delete import build_parser


def test_delete_parser_single_mode():
    args = build_parser().parse_args([
        "--message-id", "m1",
        "--confirm",
    ])
    assert args.message_id == "m1"
    assert args.confirm is True


def test_delete_parser_bulk_plan_out():
    args = build_parser().parse_args([
        "--from", "alice@example.com",
        "--subject", "spam",
        "--folder", "/Inbox",
        "--plan-out", "/tmp/p.json",
    ])
    assert args.from_address == "alice@example.com"
    assert args.subject_contains == "spam"
    assert args.folder == "/Inbox"
    assert args.plan_out == "/tmp/p.json"
    assert args.confirm is False


def test_delete_parser_from_plan():
    args = build_parser().parse_args([
        "--from-plan", "/tmp/p.json",
        "--confirm",
    ])
    assert args.from_plan == "/tmp/p.json"
    assert args.confirm is True


def test_delete_parser_no_args_still_valid():
    args = build_parser().parse_args([])
    assert args.message_id is None
    assert args.from_plan is None
    assert args.plan_out is None


def test_delete_help_mentions_hard_delete_distinction():
    """Per spec §19 Phase 4: --help explicitly distinguishes from mail-clean."""
    parser = build_parser()
    # argparse exposes description via format_help()
    help_text = parser.format_help()
    assert "clean" in help_text.lower() or "hard" in help_text.lower()
```

- [ ] **Step 2: Implement `src/m365ctl/mail/cli/delete.py`.**

```python
"""`m365ctl mail delete` — soft-delete one or more messages (→ Deleted Items).

This is the SOFT delete: messages move to the Deleted Items folder and can
be restored via ``m365ctl undo``. For hard/permanent delete see
``m365ctl mail clean`` (Phase 6 — arrives after Phase 4).

Three modes:
1. Single-item: `--message-id <id> --confirm`.
2. Bulk dry-run: filter flags + `--plan-out <file>`.
3. Bulk execute: `--from-plan <file> --confirm`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, load_plan, new_op_id
from m365ctl.mail.cli._bulk import (
    MessageFilter,
    confirm_bulk_proceed,
    emit_plan,
    expand_messages_for_pattern,
)
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.folders import FolderNotFound, resolve_folder_path
from m365ctl.mail.messages import get_message
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.delete import execute_soft_delete


_DESCRIPTION = (
    "Soft-delete messages (move to Deleted Items). "
    "For hard/permanent delete see `mail clean` (arrives Phase 6). "
    "All soft-deletes are reversible via `m365ctl undo <op-id>`."
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail delete", description=_DESCRIPTION)
    add_common_args(p)
    p.add_argument("--confirm", action="store_true")

    # Mode 1: single-item
    p.add_argument("--message-id", help="Soft-delete one specific message.")

    # Mode 2: bulk pattern
    p.add_argument("--folder", default="Inbox",
                   help="Source folder (default: Inbox). Used in bulk mode.")
    p.add_argument("--from", dest="from_address",
                   help="Filter by sender address.")
    p.add_argument("--subject", dest="subject_contains",
                   help="Filter by substring in subject.")
    p.add_argument("--since", help="ISO-8601 lower bound on receivedDateTime.")
    p.add_argument("--until", help="ISO-8601 upper bound on receivedDateTime.")
    p.add_argument("--unread", action="store_true")
    p.add_argument("--read", action="store_true")
    p.add_argument("--has-attachments", action="store_true")
    p.add_argument("--importance", choices=("low", "normal", "high"))
    p.add_argument("--focus", choices=("focused", "other"))
    p.add_argument("--category")

    # Plan plumbing
    p.add_argument("--plan-out", help="Write plan to this path and exit (dry run).")
    p.add_argument("--from-plan", help="Execute ops from this plan file (requires --confirm).")

    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--page-size", type=int, default=50)
    return p


def _build_filter(args) -> MessageFilter:
    if args.unread and args.read:
        return MessageFilter()
    unread_flag: bool | None = None
    if args.unread:
        unread_flag = True
    elif args.read:
        unread_flag = False
    return MessageFilter(
        unread=unread_flag,
        from_address=args.from_address,
        subject_contains=args.subject_contains,
        since=args.since,
        until=args.until,
        has_attachments=True if args.has_attachments else None,
        importance=args.importance,
        focus=args.focus,
        category=args.category,
    )


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    # --- From-plan mode (bulk execute) --------------------------------------
    if args.from_plan:
        if not args.confirm:
            print("mail delete --from-plan requires --confirm.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        plan = load_plan(Path(args.from_plan))
        ops = [op for op in plan.operations if op.action == "mail.delete.soft"]
        if not ops:
            print("mail delete --from-plan: no mail.delete.soft ops in plan.", file=sys.stderr)
            return 2
        if not confirm_bulk_proceed(len(ops), verb="delete"):
            print("aborted: user declined /dev/tty confirm.", file=sys.stderr)
            return 2
        token = cred.get_token()
        graph = GraphClient(token_provider=lambda: token)
        logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
        any_error = False
        for op in ops:
            op.args.setdefault("auth_mode", auth_mode)
            try:
                msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                                  message_id=op.item_id)
                before = {"parent_folder_id": msg.parent_folder_id,
                          "parent_folder_path": msg.parent_folder_path}
            except Exception:
                before = {}
            result = execute_soft_delete(op, graph, logger, before=before)
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    # --- Single-item mode ---------------------------------------------------
    if args.message_id:
        cfg, auth_mode, cred = load_and_authorize(args)
        assert_mail_target_allowed(
            cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
            unsafe_scope=args.unsafe_scope,
        )
        if not args.confirm:
            print(f"(dry-run) would soft-delete {args.message_id} (→ Deleted Items)",
                  file=sys.stderr)
            return 0
        token = cred.get_token()
        graph = GraphClient(token_provider=lambda: token)
        try:
            msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                              message_id=args.message_id)
            before = {"parent_folder_id": msg.parent_folder_id,
                      "parent_folder_path": msg.parent_folder_path}
        except Exception:
            before = {}
        op = Operation(
            op_id=new_op_id(), action="mail.delete.soft",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
            args={"auth_mode": auth_mode},
        )
        logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
        result = execute_soft_delete(op, graph, logger, before=before)
        if result.status != "ok":
            print(f"error: {result.error}", file=sys.stderr)
            return 1
        print(f"[{op.op_id}] ok — soft-deleted {args.message_id} (→ Deleted Items)")
        return 0

    # --- Bulk plan-out mode -------------------------------------------------
    if args.unread and args.read:
        print("mail delete: --unread and --read are mutually exclusive", file=sys.stderr)
        return 2

    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope, folder_path=args.folder,
    )
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        source_folder_id = resolve_folder_path(
            args.folder, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
    except FolderNotFound as e:
        print(f"mail delete: {e}", file=sys.stderr)
        return 2

    msgs = list(expand_messages_for_pattern(
        graph=graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        resolved_folders=[(source_folder_id, args.folder)],
        filter=_build_filter(args),
        limit=args.limit, page_size=args.page_size,
    ))
    if not msgs:
        print("mail delete: no matching messages; nothing to do.")
        return 0

    ops = [
        Operation(
            op_id=new_op_id(), action="mail.delete.soft",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=m.id,
            args={"auth_mode": auth_mode},
            dry_run_result=f"would soft-delete {m.id} ({m.subject!r})",
        )
        for m in msgs
    ]

    if args.plan_out:
        emit_plan(
            Path(args.plan_out),
            source_cmd=f"mail delete --from {args.from_address or '?'} --folder {args.folder}",
            scope=derive_mailbox_upn(args.mailbox),
            operations=ops,
        )
        print(f"Wrote plan with {len(ops)} soft-delete ops to {args.plan_out}.")
        print(f"Review, then: mail delete --from-plan {args.plan_out} --confirm")
        return 0

    # No --plan-out: preview and exit.
    print(f"mail delete: matched {len(msgs)} messages. Pass --plan-out <path> to persist, "
          f"then --from-plan <path> --confirm to execute.")
    for op in ops[:10]:
        print(f"  {op.dry_run_result}")
    if len(ops) > 10:
        print(f"  ... and {len(ops) - 10} more")
    return 0
```

- [ ] **Step 3: Route `delete` in `mail/cli/__main__.py`.**

Read `src/m365ctl/mail/cli/__main__.py`. Find the `elif verb == "categorize":` branch (last Phase 3 entry, added in Phase 3 G9). Immediately AFTER it, add:
```python
    elif verb == "delete":
        from m365ctl.mail.cli.delete import main as f
```

Also update the `_USAGE` text. Find the "Mutations (safe — all undoable):" section and add a new line inside it:
```
  delete       soft-delete messages (→ Deleted Items)
```
Place it under `categorize` for alphabetical-ish order within the mutation list, or just after it — whichever matches the existing order. Also add a trailing note at the bottom of `_USAGE`:
```
"\nHard delete (permanent) lands in Phase 6 — `mail clean`. Use with care.\n"
```
(Append this just before the closing paren of the f-string-free `_USAGE = (...)`. Preserve the existing format.)

- [ ] **Step 4: Create `bin/mail-delete`.**

```bash
cat > bin/mail-delete <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec uv run --project "$REPO" python -m m365ctl mail delete "$@"
EOF
chmod +x bin/mail-delete
```

- [ ] **Step 5: Smoke.**

```bash
./bin/mail-delete --help 2>&1 | head -5
uv run python -m m365ctl mail --help | grep -i delete
```

Both must exit 0. First should show the argparse banner; second should list the new `delete` verb and the hard-delete Phase 6 note.

- [ ] **Step 6: Run + commit.**

```bash
uv run pytest tests/test_cli_mail_delete.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/cli/delete.py src/m365ctl/mail/cli/__main__.py bin/mail-delete tests/test_cli_mail_delete.py
git commit -m "feat(mail/cli): mail delete — soft delete with single + bulk-plan workflows"
```

Expected suite: 474 + 5 = 479.

---

## Group 4: Release 0.5.0 + push/PR/merge

### Task 4: Bump + CHANGELOG + plan file + gates

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`
- The plan file is currently untracked; commit it on the branch.

- [ ] **Step 1: Bump pyproject.**

`pyproject.toml`: `version = "0.4.0"` → `version = "0.5.0"`.

- [ ] **Step 2: CHANGELOG entry.**

Open `CHANGELOG.md`. Insert a new `## [0.5.0] — 2026-04-25` section above `## [0.4.0] — 2026-04-25`:

```markdown
## [0.5.0] — 2026-04-25

### Added
- **`m365ctl mail delete` — soft delete via move-to-Deleted-Items.** Single-item (`--message-id --confirm`) or bulk-plan (`--from --subject --folder --plan-out` → review → `--from-plan --confirm`). Bulk ≥20 ops require interactive `/dev/tty` confirm.
- `src/m365ctl/mail/mutate/delete.py` — `execute_soft_delete`: `POST /messages/{id}/move {"destinationId": "deleteditems"}`.
- `bin/mail-delete` short wrapper; dispatcher route for `mail delete` verb.
- `--help` explicitly distinguishes soft delete from the hard-delete `mail clean` verb (Phase 6).

### Changed
- **`m365ctl undo <op-id>` now reverses `mail.delete.soft` ops** — moves the message back to its original parent folder using `before.parent_folder_id` captured at delete time.
- **Closed the Phase 3 `mail.copy` undo chain.** The copy's inverse (`mail.delete.soft` on the new message id) now runs end-to-end: `m365ctl undo <copy-op-id>` soft-deletes the copy instead of printing a Phase 4 deferral message.
- `mail/mutate/undo.py`: `build_reverse_mail_operation` grew a `cmd == "mail-delete-soft"` branch. The Dispatcher's `mail.delete.soft` inverse returns a real `(before, after) → mail.move` spec (replacing the Phase 3 placeholder).
- `mail/cli/undo.py`: the `action == "mail.delete.soft"` branch now calls `execute_soft_delete` (replacing the Phase 3 deferral print).

### Deferred
- Hard delete (`mail clean`) — Phase 6. Uses `DELETE /messages/{id}`; bypasses Deleted Items; irreversible.
- ETag 412 → refresh → retry loop still deferred (Phase 3.5 or later).
```

- [ ] **Step 3: Commit release.**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore(release): bump to 0.5.0 + CHANGELOG entry for mail soft-delete + restore"
```

- [ ] **Step 4: Commit plan file.**

```bash
git add docs/superpowers/plans/2026-04-25-phase-4-mail-soft-delete-restore.md
git commit -m "docs(plans): commit Phase 4 mail soft-delete plan"
```

- [ ] **Step 5: Final gates.**

```bash
uv run pytest -m "not live" -q 2>&1 | tail -3
uv run ruff check 2>&1 | tail -5
uv run mypy src 2>&1 | tail -10
```

Expect: 479 passed, 1 deselected. Ruff clean. Mypy likely +2–4 over Phase 3's 68 (one new mutate module + one new CLI) — record the count.

`--help` smoke:
```bash
uv run python -m m365ctl mail delete --help
./bin/mail-delete --help
uv run python -m m365ctl undo --help
```

All exit 0.

If ruff complains, auto-fix and commit as `fix(lint): ...`.

- [ ] **Step 6: Push + PR + merge.**

```bash
git push -u origin phase-4-mail-soft-delete-restore
gh pr create --title "Phase 4: mail soft-delete + restore, closes copy undo chain (0.5.0)" --body "..."
gh pr checks <N> --watch
gh pr merge <N> --merge --delete-branch
git checkout main && git pull
```

## User-performed live-tenant smoke (after merge)

```bash
# 1. Soft-delete single message + undo round-trip
./bin/mail-delete --message-id <id> --confirm
# verify in Outlook: message is in Deleted Items
./bin/m365ctl-undo <op-id> --confirm
# verify: message is back in the original folder

# 2. Bulk plan workflow
./bin/mail-delete --from spam@example.com --folder Inbox --plan-out plan.json
head plan.json
./bin/mail-delete --from-plan plan.json --confirm

# 3. Closes the Phase 3 copy undo chain
./bin/mail-copy --message-id <id> --to-folder /Archive --confirm
./bin/m365ctl-undo <copy-op-id> --confirm
# the copy (not the original) is now soft-deleted — verify in Deleted Items
```

---

## Self-review

**1. Spec coverage (§19 Phase 4 deliverables):**
- [x] `mail.mutate.delete.execute_soft_delete` — Task 1.
- [x] `inverse_soft_delete` — Task 2 (implemented as the combined `cmd == "mail-delete-soft"` branch + Dispatcher lambda).
- [x] CLI `mail-delete` — Task 3.
- [x] Plan schema `action: mail.delete.soft` — already accepted in `_VALID_ACTIONS` (Phase 3 G1). Reaffirmed here.
- [x] `--help` explicitly distinguishes from `mail-clean` — Task 3 `_DESCRIPTION` + `_USAGE` trailing note + parser test `test_delete_help_mentions_hard_delete_distinction`.
- [x] Bump to 0.5.0 — Task 4.

**2. Acceptance (§19 Phase 4):**
- `mail-delete --message-id … --confirm` moves to Deleted Items — Task 1 + Task 3 single-item path.
- `m365ctl undo <op> --confirm` restores — Task 2 wires the reverse builder + CLI dispatch.
- Bulk via plan works — Task 3 `--plan-out`/`--from-plan` modes.
- "Not found" error on manually-purged message — Graph returns 404 via `GraphError`; Task 1's error branch surfaces it cleanly.

**3. Placeholder scan:** No TODOs outside explicit "Phase 6 / Phase 3.5" deferrals in `_DESCRIPTION` + CHANGELOG.

**4. Type consistency:**
- `execute_soft_delete(op, graph, logger, *, before) -> MailResult` — consistent with other Phase 3 executors.
- `after = {"parent_folder_id": <new>, "deleted_from": <old>}` — a new but lightweight shape; documented in the test.
- Reverse builder returns `Operation(action="mail.move", ...)` with `args["destination_id"] = before["parent_folder_id"]` — matches what `execute_move` expects.
- Dispatcher lambda signature `(b, a) -> dict` — matches existing pattern.

---

Plan complete.
