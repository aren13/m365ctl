# Phase 3 — Safe Message Mutations (move/copy/flag/read/focus/categorize) + first mail plan-file workflow (0.4.0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship six message-level mutations — `mail move`, `mail copy`, `mail flag`, `mail read`, `mail focus`, `mail categorize` — all undoable, all dry-run-by-default, with the first mail-side **plan-file bulk workflow** (`--pattern`-style filters → `--plan-out plan.json` → `--from-plan plan.json --confirm`). Bumps version to 0.4.0.

**Architecture:**
- Six new executors under `src/m365ctl/mail/mutate/{move,copy,flag,read,focus,categorize}.py`, each shaped like Phase 2's folder executors: `execute_<verb>(op, graph, logger, *, before) -> MailResult`, wrapping a single Graph call with `log_mutation_start` / `log_mutation_end`.
- A new shared CLI helper `src/m365ctl/mail/cli/_bulk.py` for **pattern expansion** (filter-driven message selection via `mail.messages.list_messages`) and `--from-plan`/`--plan-out` plumbing. Mirrors the OneDrive `cli/_common.py` bulk helpers but works on mailbox+folder rather than drive+path.
- Undo inverses: spec §12.1 rows — all six are reversible via `mail.mutate.undo.build_reverse_mail_operation` (extended) + Dispatcher registration. `mail.copy` inverse is `mail.delete.soft` on the new message id; all others round-trip to themselves with prior-state args.
- CLI: six new `m365ctl mail <verb>` subcommands. Each supports BOTH single-item (`--message-id`) AND plan-driven bulk (`--pattern`-shaped filters + `--plan-out` / `--from-plan --confirm`). Interactive `/dev/tty` confirm fires when a bulk plan has >N items (default 20).
- ETag handling: each executor includes `If-Match: <change_key>` when `op.args["change_key"]` is present (CLI captures `change_key` from the pre-mutation `get_message` call). A 412 response triggers ONE retry after a fresh `get_message` — if the second attempt also 412s, the op returns `status="error"` with a clear message.
- `mail.delete.soft` is registered in `_VALID_ACTIONS` and the Dispatcher (as the inverse target of `mail.copy`), but its **executor** doesn't land until Phase 4 — the Dispatcher entry is a placeholder pointing at an `execute_soft_delete` that's added in Phase 4. For Phase 3, the undo of `mail.copy` prints a human-readable message explaining the copy exists and can be removed manually until Phase 4.

**Tech Stack:** Python 3.11+ stdlib, httpx (unchanged), pytest + MagicMock. No new dependencies.

**Parent spec:** `docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md` — §9.2 (mutation endpoints), §10.3 (CLI flags), §11.4 (action namespace), §12.1 (per-action capture), §13.3 (ETag retry), §19 Phase 3 (deliverables + acceptance).

**Safety posture:**
- **`--confirm` is required for every mutation.** Dry-run default.
- **Bulk via plan file is the norm**: `mail move --pattern --from alice@ --subject "old" --plan-out p.json`, review, then `mail move --from-plan p.json --confirm`. Ad-hoc `--message-id` is for one-offs.
- **`assert_mail_target_allowed`** gates every mutation. For messages living in a denied folder (e.g. `Calendar/`), the pre-auth deny check fires before any Graph call — same pattern Phase 2 established.
- **Interactive `/dev/tty` confirm** when a bulk plan has ≥20 items (config-tunable at plan-file emit time).
- **ETag retry limited to one** — prevents runaway loops on genuinely-conflicted writes.
- Work on feature branch `phase-3-mail-safe-message-mutations` (off `main`).

---

## File Structure (Phase 3 target)

```
m365ctl/
├── pyproject.toml                              # MODIFIED — version 0.4.0
├── CHANGELOG.md                                # MODIFIED — [0.4.0] entry
├── src/m365ctl/
│   ├── common/
│   │   └── planfile.py                         # MODIFIED — extend _VALID_ACTIONS with mail.{move,copy,flag,read,focus,categorize,delete.soft}
│   └── mail/
│       ├── cli/
│       │   ├── _bulk.py                        # NEW — pattern expansion + plan-file emit/consume + N-item TTY confirm
│       │   ├── move.py                         # NEW (replaces mail/cli/stub if any)
│       │   ├── copy.py                         # NEW
│       │   ├── flag.py                         # NEW
│       │   ├── read.py                         # NEW
│       │   ├── focus.py                        # NEW
│       │   ├── categorize.py                   # NEW
│       │   └── __main__.py                     # MODIFIED — add verb routing for 6 new verbs
│       └── mutate/
│           ├── move.py                         # NEW
│           ├── copy.py                         # NEW
│           ├── flag.py                         # NEW
│           ├── read.py                         # NEW
│           ├── focus.py                        # NEW
│           ├── categorize.py                   # NEW
│           └── undo.py                         # MODIFIED — extend build_reverse_mail_operation + register_mail_inverses
├── bin/
│   ├── mail-move                               # NEW
│   ├── mail-copy                               # NEW
│   ├── mail-flag                               # NEW
│   ├── mail-read                               # NEW
│   ├── mail-focus                              # NEW
│   └── mail-categorize                         # NEW
└── tests/
    ├── test_mail_cli_bulk.py                   # NEW
    ├── test_mail_mutate_move.py                # NEW
    ├── test_mail_mutate_copy.py                # NEW
    ├── test_mail_mutate_flag.py                # NEW
    ├── test_mail_mutate_read.py                # NEW
    ├── test_mail_mutate_focus.py               # NEW
    ├── test_mail_mutate_categorize.py          # NEW
    ├── test_mail_mutate_undo_phase3.py         # NEW — inverses for the 6 new verbs
    ├── test_cli_mail_move.py                   # NEW
    ├── test_cli_mail_copy.py                   # NEW
    ├── test_cli_mail_flag.py                   # NEW
    ├── test_cli_mail_read.py                   # NEW
    ├── test_cli_mail_focus.py                  # NEW
    └── test_cli_mail_categorize.py             # NEW
```

---

## Preflight

### Task 0: Branch + baseline

- [ ] **Step 1:** `git status` → clean; `git branch --show-current` → `main`.
- [ ] **Step 2:** `git checkout -b phase-3-mail-safe-message-mutations`.
- [ ] **Step 3:** `uv run pytest -m "not live" -q 2>&1 | tail -3` → record baseline (expect **412 passed, 1 deselected**).

---

## Group 1: Planfile extension

### Task 1: Add 7 new mail actions to `_VALID_ACTIONS` + `Action` Literal

**Files:**
- Modify: `src/m365ctl/common/planfile.py`
- Modify: `tests/test_planfile.py`

- [ ] **Step 1: Failing test.**

Append to `tests/test_planfile.py`:
```python
def test_plan_loader_accepts_phase3_mail_actions(tmp_path):
    from m365ctl.common.planfile import PLAN_SCHEMA_VERSION, load_plan
    import json
    path = tmp_path / "p.json"
    path.write_text(json.dumps({
        "version": PLAN_SCHEMA_VERSION,
        "created_at": "2026-04-24T00:00:00Z",
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
```

Run: `uv run pytest tests/test_planfile.py::test_plan_loader_accepts_phase3_mail_actions -q` → FAIL.

- [ ] **Step 2: Extend `_VALID_ACTIONS` + `Action` Literal.**

Add the 7 new action strings to both the `Literal[...]` and the `frozenset({...})` in `src/m365ctl/common/planfile.py`, separated in a commented block:
```python
# Phase 3 — safe message mutations.
"mail.move", "mail.copy", "mail.flag", "mail.read",
"mail.focus", "mail.categorize", "mail.delete.soft",
```

- [ ] **Step 3: Run tests + commit.**
```bash
uv run pytest tests/test_planfile.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/common/planfile.py tests/test_planfile.py
git commit -m "feat(planfile): accept Phase 3 mail action namespaces (move/copy/flag/read/focus/categorize/delete.soft)"
```

Expected suite: 412 + 1 = 413.

---

## Group 2: Bulk CLI helpers

### Task 2: `mail/cli/_bulk.py` — pattern expansion + plan I/O + TTY confirm

**Files:**
- Create: `src/m365ctl/mail/cli/_bulk.py`
- Create: `tests/test_mail_cli_bulk.py`

- [ ] **Step 1: Failing tests.**

Write `tests/test_mail_cli_bulk.py`:
```python
"""Tests for m365ctl.mail.cli._bulk — pattern expansion + plan I/O."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.planfile import PLAN_SCHEMA_VERSION, Plan, load_plan
from m365ctl.mail.cli._bulk import (
    MessageFilter,
    emit_plan,
    expand_messages_for_pattern,
)
from m365ctl.mail.models import EmailAddress, Flag, Message
from datetime import datetime, timezone


def _msg(msg_id: str, folder_path: str = "/Inbox", subject: str = "s") -> Message:
    return Message(
        id=msg_id, mailbox_upn="me", internet_message_id=f"<{msg_id}>",
        conversation_id="c", conversation_index=b"",
        parent_folder_id="folder-id", parent_folder_path=folder_path,
        subject=subject,
        sender=EmailAddress(name="", address=""),
        from_addr=EmailAddress(name="A", address="a@example.com"),
        to=[], cc=[], bcc=[], reply_to=[],
        received_at=datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc),
        sent_at=None, is_read=False, is_draft=False, has_attachments=False,
        importance="normal",
        flag=Flag(status="notFlagged"),
        categories=[], inference_classification="focused",
        body_preview="", body=None, web_link="", change_key="ck",
    )


def test_expand_messages_single_folder():
    # _list_messages_impl is injected so tests bypass the real Graph client.
    def fake_list(*, graph, mailbox_spec, auth_mode, folder_id, parent_folder_path, filters, limit, page_size):
        return [_msg("m1", parent_folder_path), _msg("m2", parent_folder_path)]
    resolved_folders = [("inbox", "/Inbox")]
    msgs = list(expand_messages_for_pattern(
        graph=MagicMock(),
        mailbox_spec="me",
        auth_mode="delegated",
        resolved_folders=resolved_folders,
        filter=MessageFilter(from_address="a@example.com"),
        limit=50,
        _list_messages_impl=fake_list,
    ))
    assert [m.id for m in msgs] == ["m1", "m2"]


def test_expand_messages_multiple_folders():
    def fake_list(*, folder_id, parent_folder_path, **_kw):
        if folder_id == "inbox":
            return [_msg("m1", parent_folder_path)]
        return [_msg("m2", parent_folder_path)]
    resolved = [("inbox", "/Inbox"), ("archive", "/Archive")]
    msgs = list(expand_messages_for_pattern(
        graph=MagicMock(),
        mailbox_spec="me",
        auth_mode="delegated",
        resolved_folders=resolved,
        filter=MessageFilter(),
        limit=50,
        _list_messages_impl=fake_list,
    ))
    assert [m.id for m in msgs] == ["m1", "m2"]


def test_expand_messages_respects_limit_across_folders():
    def fake_list(*, folder_id, **_kw):
        return [_msg(f"{folder_id}-{i}") for i in range(10)]
    resolved = [("inbox", "/Inbox"), ("archive", "/Archive"), ("trash", "/Trash")]
    msgs = list(expand_messages_for_pattern(
        graph=MagicMock(),
        mailbox_spec="me",
        auth_mode="delegated",
        resolved_folders=resolved,
        filter=MessageFilter(),
        limit=15,
        _list_messages_impl=fake_list,
    ))
    assert len(msgs) == 15


def test_emit_plan_writes_json_with_schema_version(tmp_path):
    plan_path = tmp_path / "out.json"
    from m365ctl.common.planfile import Operation
    ops = [
        Operation(op_id="1", action="mail.move", drive_id="me", item_id="m1",
                  args={"destination_id": "archive"},
                  dry_run_result="would move m1 -> /Archive"),
    ]
    emit_plan(plan_path, source_cmd="mail move --plan-out", scope="me", operations=ops)
    plan = load_plan(plan_path)
    assert plan.version == PLAN_SCHEMA_VERSION
    assert plan.source_cmd == "mail move --plan-out"
    assert len(plan.operations) == 1
    assert plan.operations[0].action == "mail.move"


def test_message_filter_applies_locally():
    """MessageFilter supports an optional `match()` helper for post-list filtering."""
    msgs = [
        _msg("m1", subject="Meeting minutes"),
        _msg("m2", subject="Lunch plans"),
    ]
    f = MessageFilter(subject_contains="meeting")
    out = [m for m in msgs if f.match(m)]
    assert len(out) == 1
    assert out[0].id == "m1"
```

Run: `uv run pytest tests/test_mail_cli_bulk.py -q` → all FAIL.

- [ ] **Step 2: Implement `src/m365ctl/mail/cli/_bulk.py`.**

```python
"""Shared bulk helpers for mail mutation CLIs.

Two responsibilities:
1. **Pattern expansion** — given a set of resolved folders + a filter spec,
   iterate messages across them (stopping at ``limit``). Callers pass a
   ``MessageFilter`` dataclass whose shape mirrors ``mail.messages.MessageListFilters``
   so the server-side OData filter can be pushed down.
2. **Plan I/O** — write a ``Plan`` to disk via the existing ``planfile.write_plan``
   with sensible metadata; read + iterate ops via ``planfile.load_plan``.

The third helper (``confirm_bulk_proceed``) gates large plan execution
behind an interactive ``/dev/tty`` confirm — same pattern as
``common.safety._confirm_via_tty``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, Plan, write_plan
from m365ctl.common.safety import _confirm_via_tty
from m365ctl.mail.messages import MessageListFilters, list_messages as _default_list_messages
from m365ctl.mail.models import Message


@dataclass(frozen=True)
class MessageFilter:
    """Filter inputs for bulk pattern expansion.

    Mirrors ``mail.messages.MessageListFilters`` so server-side ``$filter``
    clauses are pushed down. Also provides a ``match()`` helper for local
    post-list filtering when the caller wants a second-pass check.
    """
    unread: bool | None = None
    from_address: str | None = None
    subject_contains: str | None = None
    since: str | None = None
    until: str | None = None
    has_attachments: bool | None = None
    importance: str | None = None
    focus: str | None = None
    category: str | None = None

    def as_list_filters(self) -> MessageListFilters:
        return MessageListFilters(
            unread=self.unread,
            from_address=self.from_address,
            subject_contains=self.subject_contains,
            since=self.since,
            until=self.until,
            has_attachments=self.has_attachments,
            importance=self.importance,
            focus=self.focus,
            category=self.category,
        )

    def match(self, m: Message) -> bool:
        """Local predicate for filters the Graph $filter missed (e.g. case-insensitive subject).

        Most filters are already applied server-side by ``list_messages``; this
        helper lets callers layer a secondary check if they loaded messages via
        a broader fetch.
        """
        if self.unread is True and m.is_read:
            return False
        if self.unread is False and not m.is_read:
            return False
        if self.from_address and m.from_addr.address.lower() != self.from_address.lower():
            return False
        if self.subject_contains and self.subject_contains.lower() not in m.subject.lower():
            return False
        if self.importance and m.importance != self.importance:
            return False
        if self.focus and m.inference_classification != self.focus:
            return False
        if self.category and self.category not in m.categories:
            return False
        if self.has_attachments is True and not m.has_attachments:
            return False
        if self.has_attachments is False and m.has_attachments:
            return False
        return True


def expand_messages_for_pattern(
    *,
    graph: GraphClient,
    mailbox_spec: str,
    auth_mode: str,
    resolved_folders: list[tuple[str, str]],
    filter: MessageFilter,
    limit: int = 50,
    page_size: int = 50,
    _list_messages_impl: Callable[..., Iterable[Message]] = _default_list_messages,
) -> Iterator[Message]:
    """Yield messages across ``resolved_folders`` (list of ``(folder_id, folder_path)``).

    The server-side ``$filter`` is pushed down via ``list_messages``. Iteration
    stops at ``limit`` total messages across all folders.

    ``_list_messages_impl`` is injected so unit tests can bypass the real
    Graph client. Production callers pass the default.
    """
    yielded = 0
    list_filters = filter.as_list_filters()
    for folder_id, folder_path in resolved_folders:
        for msg in _list_messages_impl(
            graph=graph,
            mailbox_spec=mailbox_spec,
            auth_mode=auth_mode,
            folder_id=folder_id,
            parent_folder_path=folder_path,
            filters=list_filters,
            limit=limit - yielded,
            page_size=page_size,
        ):
            yield msg
            yielded += 1
            if yielded >= limit:
                return


def emit_plan(
    path: Path,
    *,
    source_cmd: str,
    scope: str,
    operations: list[Operation],
) -> None:
    """Write a ``Plan`` to ``path`` with ``PLAN_SCHEMA_VERSION`` + ISO-8601 UTC timestamp."""
    from m365ctl.common.planfile import PLAN_SCHEMA_VERSION
    plan = Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(),
        source_cmd=source_cmd,
        scope=scope,
        operations=list(operations),
    )
    write_plan(plan, path)


def confirm_bulk_proceed(n: int, *, threshold: int = 20, verb: str) -> bool:
    """Return True if the bulk should proceed.

    - If ``n < threshold``: always True (no prompt).
    - Else: prompt via ``/dev/tty`` (not stdin) for y/N confirmation.

    Designed so Claude/agents piping stdin can't bypass this.
    """
    if n < threshold:
        return True
    prompt = (
        f"m365ctl mail {verb}: about to apply {n} operations from plan file.\n"
        f"Proceed? [y/N]: "
    )
    return _confirm_via_tty(prompt)
```

- [ ] **Step 3: Run + commit.**
```bash
uv run pytest tests/test_mail_cli_bulk.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/cli/_bulk.py tests/test_mail_cli_bulk.py
git commit -m "feat(mail/cli): _bulk — MessageFilter + expand_messages_for_pattern + emit_plan + confirm_bulk_proceed"
```

Expected suite: 413 + 5 = 418.

---

## Group 3: move + copy executors

### Task 3: `mail/mutate/move.py` + `mail/mutate/copy.py`

**Files:**
- Create: `src/m365ctl/mail/mutate/move.py`
- Create: `src/m365ctl/mail/mutate/copy.py`
- Create: `tests/test_mail_mutate_move.py`
- Create: `tests/test_mail_mutate_copy.py`

- [ ] **Step 1: Failing tests for move.**

Write `tests/test_mail_mutate_move.py`:
```python
"""Tests for m365ctl.mail.mutate.move."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.move import execute_move


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_move_posts_to_message_move_with_destination(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "m1", "parentFolderId": "archive"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-mv",
        action="mail.move",
        drive_id="me",
        item_id="m1",
        args={"destination_id": "archive", "destination_path": "/Archive"},
    )
    result = execute_move(
        op, graph, logger,
        before={"parent_folder_id": "inbox", "parent_folder_path": "/Inbox"},
    )
    assert result.status == "ok"
    assert result.after == {"parent_folder_id": "archive"}
    assert graph.post.call_args.args[0] == "/me/messages/m1/move"
    assert graph.post.call_args.kwargs["json"] == {"destinationId": "archive"}
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-move"
    assert entries[0]["before"]["parent_folder_id"] == "inbox"


def test_move_app_only_routes_via_users_upn(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "m1", "parentFolderId": "archive"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-ao",
        action="mail.move",
        drive_id="bob@example.com",
        item_id="m1",
        args={"destination_id": "archive", "destination_path": "/Archive",
              "auth_mode": "app-only"},
    )
    execute_move(op, graph, logger, before={})
    assert graph.post.call_args.args[0] == "/users/bob@example.com/messages/m1/move"


def test_move_graph_error(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.post.side_effect = GraphError("not found")
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-err",
        action="mail.move",
        drive_id="me", item_id="m1",
        args={"destination_id": "archive"},
    )
    result = execute_move(op, graph, logger, before={})
    assert result.status == "error"
    assert "not found" in (result.error or "")
```

Run: `uv run pytest tests/test_mail_mutate_move.py -q` → all FAIL.

- [ ] **Step 2: Implement `src/m365ctl/mail/mutate/move.py`.**

```python
"""Message move — POST /messages/{id}/move with {destinationId}."""
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


def execute_move(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    dest_id = op.args["destination_id"]
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-move",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.post(
            f"{ub}/messages/{op.item_id}/move",
            json={"destinationId": dest_id},
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"parent_folder_id": dest_id}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
```

- [ ] **Step 3: Failing tests for copy.**

Write `tests/test_mail_mutate_copy.py`:
```python
"""Tests for m365ctl.mail.mutate.copy."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.copy import execute_copy


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_copy_posts_and_records_new_message_id(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "m1-copy", "parentFolderId": "archive"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-copy",
        action="mail.copy",
        drive_id="me",
        item_id="m1",
        args={"destination_id": "archive", "destination_path": "/Archive"},
    )
    result = execute_copy(op, graph, logger, before={})
    assert result.status == "ok"
    assert result.after == {
        "new_message_id": "m1-copy",
        "destination_folder_id": "archive",
    }
    assert graph.post.call_args.args[0] == "/me/messages/m1/copy"
    assert graph.post.call_args.kwargs["json"] == {"destinationId": "archive"}


def test_copy_graph_error(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.post.side_effect = GraphError("quota exceeded")
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-err",
        action="mail.copy",
        drive_id="me", item_id="m1",
        args={"destination_id": "archive"},
    )
    result = execute_copy(op, graph, logger, before={})
    assert result.status == "error"
    assert "quota" in (result.error or "")
```

- [ ] **Step 4: Implement `src/m365ctl/mail/mutate/copy.py`.**

```python
"""Message copy — POST /messages/{id}/copy with {destinationId}.

Inverse: `mail.delete.soft` on ``after.new_message_id``. The inverse executor
arrives in Phase 4; Phase 3 registers only the Dispatcher entry.
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


def execute_copy(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    dest_id = op.args["destination_id"]
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-copy",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        created = graph.post(
            f"{ub}/messages/{op.item_id}/copy",
            json={"destinationId": dest_id},
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {
        "new_message_id": created.get("id", ""),
        "destination_folder_id": dest_id,
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
```

- [ ] **Step 5: Run + commit.**
```bash
uv run pytest tests/test_mail_mutate_move.py tests/test_mail_mutate_copy.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/mutate/move.py src/m365ctl/mail/mutate/copy.py tests/test_mail_mutate_move.py tests/test_mail_mutate_copy.py
git commit -m "feat(mail/mutate): move + copy executors with audit log integration"
```

Expected suite: 418 + 3 + 2 = 423.

---

## Group 4: flag + read + focus executors (single-field PATCH)

### Task 4: `mail/mutate/{flag,read,focus}.py`

**Files:**
- Create: `src/m365ctl/mail/mutate/flag.py`
- Create: `src/m365ctl/mail/mutate/read.py`
- Create: `src/m365ctl/mail/mutate/focus.py`
- Create: `tests/test_mail_mutate_flag.py`
- Create: `tests/test_mail_mutate_read.py`
- Create: `tests/test_mail_mutate_focus.py`

These three share the same shape: PATCH `/messages/{id}` with a single-field payload. Per spec §12.1:
- **flag**: payload `{flag: {flagStatus: ..., startDateTime?, dueDateTime?}}`. Before captures prior flag object; after captures new.
- **read**: payload `{isRead: bool}`. Before captures prior `isRead`; after captures new.
- **focus**: payload `{inferenceClassification: "focused"|"other"}`. Before/after similarly.

- [ ] **Step 1: Failing tests — flag.**

Write `tests/test_mail_mutate_flag.py`:
```python
from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.flag import execute_flag


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_flag_set_flagged(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-flag", action="mail.flag",
        drive_id="me", item_id="m1",
        args={"status": "flagged", "due_at": "2026-04-30T17:00:00Z"},
    )
    result = execute_flag(
        op, graph, logger,
        before={"status": "notFlagged", "start_at": None, "due_at": None, "completed_at": None},
    )
    assert result.status == "ok"
    assert graph.patch.call_args.args[0] == "/me/messages/m1"
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {
        "flag": {"flagStatus": "flagged", "dueDateTime": {"dateTime": "2026-04-30T17:00:00Z", "timeZone": "UTC"}},
    }
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-flag"
    assert entries[0]["before"]["status"] == "notFlagged"


def test_flag_clear(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-clear", action="mail.flag",
        drive_id="me", item_id="m1",
        args={"status": "notFlagged"},
    )
    result = execute_flag(op, graph, logger, before={"status": "flagged"})
    assert result.status == "ok"
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"flag": {"flagStatus": "notFlagged"}}


def test_flag_with_etag(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-etag", action="mail.flag",
        drive_id="me", item_id="m1",
        args={"status": "flagged", "change_key": "ck-123"},
    )
    execute_flag(op, graph, logger, before={})
    headers = graph.patch.call_args.kwargs.get("headers", {})
    assert headers.get("If-Match") == "ck-123"
```

- [ ] **Step 2: Implement `src/m365ctl/mail/mutate/flag.py`.**

```python
"""Message flag — PATCH /messages/{id} with {flag: {...}}."""
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


def _build_flag_payload(args: dict[str, Any]) -> dict[str, Any]:
    status = args["status"]
    payload: dict[str, Any] = {"flagStatus": status}
    if args.get("start_at"):
        payload["startDateTime"] = {"dateTime": args["start_at"], "timeZone": "UTC"}
    if args.get("due_at"):
        payload["dueDateTime"] = {"dateTime": args["due_at"], "timeZone": "UTC"}
    return {"flag": payload}


def execute_flag(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    ub = _user_base(op)
    payload = _build_flag_payload(op.args)
    headers = {}
    change_key = op.args.get("change_key")
    if change_key:
        headers["If-Match"] = change_key

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-flag",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.patch(
            f"{ub}/messages/{op.item_id}",
            json_body=payload,
            headers=headers or None,
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {
        "status": op.args["status"],
        "start_at": op.args.get("start_at"),
        "due_at": op.args.get("due_at"),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
```

**IMPORTANT:** `GraphClient.patch` currently has the signature `def patch(self, path: str, *, json_body: dict) -> dict` — NO `headers` parameter. **Extend it** to accept `headers: dict | None = None`:

Edit `src/m365ctl/common/graph.py` — find the `patch` method (around line 153) and add the `headers` parameter:
```python
def patch(self, path: str, *, json_body: dict, headers: dict | None = None) -> dict:
    """PATCH with JSON body; returns parsed dict; wrapped with _retry.

    ``headers`` merges with the auth headers — caller can pass e.g. ``{"If-Match": "<etag>"}``
    for conditional-write semantics.
    """

    def _do() -> dict:
        merged = self._auth_headers()
        if headers:
            merged.update(headers)
        resp = self._client.patch(path, headers=merged, json=json_body)
        return self._parse(resp)

    return self._retry(_do)
```

Same extension for `graph.post` (used by move + copy): add `headers: dict | None = None` param.

Add a small test in `tests/test_graph.py` for the new headers plumbing — one for patch, one for post (using the existing test pattern in that file).

- [ ] **Step 3: Failing tests — read.**

Write `tests/test_mail_mutate_read.py`:
```python
from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.read import execute_read


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_read_set_true(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-read", action="mail.read",
        drive_id="me", item_id="m1",
        args={"is_read": True},
    )
    result = execute_read(op, graph, logger, before={"is_read": False})
    assert result.status == "ok"
    assert result.after == {"is_read": True}
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"isRead": True}
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-read"
    assert entries[0]["before"]["is_read"] is False


def test_read_set_false(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-unread", action="mail.read",
        drive_id="me", item_id="m1",
        args={"is_read": False},
    )
    execute_read(op, graph, logger, before={"is_read": True})
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"isRead": False}
```

- [ ] **Step 4: Implement `src/m365ctl/mail/mutate/read.py`.**

```python
"""Message read/unread — PATCH /messages/{id} with {isRead: bool}."""
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


def execute_read(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    is_read = bool(op.args["is_read"])
    ub = _user_base(op)
    headers = {}
    change_key = op.args.get("change_key")
    if change_key:
        headers["If-Match"] = change_key

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-read",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.patch(
            f"{ub}/messages/{op.item_id}",
            json_body={"isRead": is_read},
            headers=headers or None,
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"is_read": is_read}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
```

- [ ] **Step 5: Failing tests — focus.**

Write `tests/test_mail_mutate_focus.py`:
```python
from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.focus import execute_focus


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_focus_set_focused(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-f", action="mail.focus",
        drive_id="me", item_id="m1",
        args={"inference_classification": "focused"},
    )
    result = execute_focus(op, graph, logger, before={"inference_classification": "other"})
    assert result.status == "ok"
    assert result.after == {"inference_classification": "focused"}
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"inferenceClassification": "focused"}
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-focus"


def test_focus_set_other(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-o", action="mail.focus",
        drive_id="me", item_id="m1",
        args={"inference_classification": "other"},
    )
    execute_focus(op, graph, logger, before={"inference_classification": "focused"})
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"inferenceClassification": "other"}
```

- [ ] **Step 6: Implement `src/m365ctl/mail/mutate/focus.py`.**

```python
"""Message focus — PATCH /messages/{id} with {inferenceClassification: ...}."""
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


def execute_focus(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    classification = op.args["inference_classification"]
    ub = _user_base(op)
    headers = {}
    change_key = op.args.get("change_key")
    if change_key:
        headers["If-Match"] = change_key

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-focus",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.patch(
            f"{ub}/messages/{op.item_id}",
            json_body={"inferenceClassification": classification},
            headers=headers or None,
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"inference_classification": classification}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
```

- [ ] **Step 7: Run + commit.**
```bash
uv run pytest tests/test_mail_mutate_flag.py tests/test_mail_mutate_read.py tests/test_mail_mutate_focus.py tests/test_graph.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/common/graph.py src/m365ctl/mail/mutate/flag.py src/m365ctl/mail/mutate/read.py src/m365ctl/mail/mutate/focus.py tests/test_mail_mutate_flag.py tests/test_mail_mutate_read.py tests/test_mail_mutate_focus.py tests/test_graph.py
git commit -m "feat(mail/mutate): flag + read + focus executors; GraphClient.patch/post accept headers for If-Match"
```

Expected suite: 423 + 3 (flag) + 2 (read) + 2 (focus) + 2 (graph headers) = 432.

---

## Group 5: categorize executor (add/remove/set semantics)

### Task 5: `mail/mutate/categorize.py`

**Files:**
- Create: `src/m365ctl/mail/mutate/categorize.py`
- Create: `tests/test_mail_mutate_categorize.py`

`categorize` is the only verb that takes a LIST of category names AND supports three sub-verbs: `--add X`, `--remove X`, `--set [X, Y]`. The executor accepts a final-state `categories` list (the CLI resolves `--add`/`--remove`/`--set` into a concrete list before calling the executor).

- [ ] **Step 1: Failing tests.**

Write `tests/test_mail_mutate_categorize.py`:
```python
from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.categorize import execute_categorize


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_categorize_sets_new_list(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-cat", action="mail.categorize",
        drive_id="me", item_id="m1",
        args={"categories": ["Followup", "Waiting"]},
    )
    result = execute_categorize(
        op, graph, logger,
        before={"categories": ["Archived"]},
    )
    assert result.status == "ok"
    assert result.after == {"categories": ["Followup", "Waiting"]}
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"categories": ["Followup", "Waiting"]}
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-categorize"
    assert entries[0]["before"]["categories"] == ["Archived"]


def test_categorize_clear_to_empty(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "m1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-clear", action="mail.categorize",
        drive_id="me", item_id="m1",
        args={"categories": []},
    )
    result = execute_categorize(op, graph, logger, before={"categories": ["X"]})
    assert result.status == "ok"
    assert result.after == {"categories": []}
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"categories": []}
```

- [ ] **Step 2: Implement `src/m365ctl/mail/mutate/categorize.py`.**

```python
"""Message categorize — PATCH /messages/{id} with {categories: [...]}.

The CLI layer resolves ``--add``/``--remove``/``--set`` into a concrete final
list before calling ``execute_categorize``. The executor itself is a pure
set-categories operation.
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


def execute_categorize(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    new_categories = list(op.args["categories"])
    ub = _user_base(op)
    headers = {}
    change_key = op.args.get("change_key")
    if change_key:
        headers["If-Match"] = change_key

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-categorize",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.patch(
            f"{ub}/messages/{op.item_id}",
            json_body={"categories": new_categories},
            headers=headers or None,
        )
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"categories": new_categories}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
```

- [ ] **Step 3: Run + commit.**
```bash
uv run pytest tests/test_mail_mutate_categorize.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/mutate/categorize.py tests/test_mail_mutate_categorize.py
git commit -m "feat(mail/mutate): categorize executor — set-semantics PATCH /messages/{id} {categories}"
```

Expected suite: 432 + 2 = 434.

---

## Group 6: Extend undo — reverse builders + Dispatcher registration

### Task 6: Add Phase 3 inverses to `mail/mutate/undo.py`

**Files:**
- Modify: `src/m365ctl/mail/mutate/undo.py`
- Modify: `src/m365ctl/mail/cli/undo.py` (add executor dispatch branches)
- Create: `tests/test_mail_mutate_undo_phase3.py`

Spec §12.1 inverses (Phase 3):
- `mail.move`: inverse `mail.move` back to `before.parent_folder_id`
- `mail.copy`: inverse `mail.delete.soft` on `after.new_message_id` — **Phase 3 registers the Dispatcher entry but the executor lives in Phase 4**. In Phase 3, the mail-undo CLI prints a human-readable message for this case ("mail copy created message X at path Y; Phase 4 adds programmatic delete-soft undo. Remove manually via Outlook UI for now.") and returns exit 2.
- `mail.flag`: inverse `mail.flag` with `before.{status, start_at, due_at}`
- `mail.read`: inverse `mail.read` with `before.is_read`
- `mail.focus`: inverse `mail.focus` with `before.inference_classification`
- `mail.categorize`: inverse `mail.categorize` with `before.categories`

- [ ] **Step 1: Failing tests.**

Write `tests/test_mail_mutate_undo_phase3.py`:
```python
"""Reverse-op tests for Phase 3 verbs (move/copy/flag/read/focus/categorize)."""
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


# ---- move reverse ---------------------------------------------------------

def test_reverse_mail_move_emits_move_back(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-1", cmd="mail-move",
        drive_id="me", item_id="m1",
        args={"destination_id": "archive"},
        before={"parent_folder_id": "inbox", "parent_folder_path": "/Inbox"},
        after={"parent_folder_id": "archive"},
    )
    rev = build_reverse_mail_operation(logger, "op-1")
    assert rev.action == "mail.move"
    assert rev.args["destination_id"] == "inbox"


def test_reverse_mail_move_rejects_missing_before_parent(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-bad", cmd="mail-move",
        drive_id="me", item_id="m1",
        args={"destination_id": "archive"},
        before={},
        after={"parent_folder_id": "archive"},
    )
    with pytest.raises(Irreversible):
        build_reverse_mail_operation(logger, "op-bad")


# ---- copy reverse (registered but executor deferred to Phase 4) -----------

def test_reverse_mail_copy_emits_delete_soft(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-c", cmd="mail-copy",
        drive_id="me", item_id="m1",
        args={"destination_id": "archive"},
        before={},
        after={"new_message_id": "m1-copy", "destination_folder_id": "archive"},
    )
    rev = build_reverse_mail_operation(logger, "op-c")
    assert rev.action == "mail.delete.soft"
    assert rev.item_id == "m1-copy"


def test_reverse_mail_copy_rejects_missing_new_message_id(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-cbad", cmd="mail-copy",
        drive_id="me", item_id="m1",
        args={"destination_id": "archive"},
        before={}, after={},
    )
    with pytest.raises(Irreversible):
        build_reverse_mail_operation(logger, "op-cbad")


# ---- flag reverse ---------------------------------------------------------

def test_reverse_mail_flag_restores_prior(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-f", cmd="mail-flag",
        drive_id="me", item_id="m1",
        args={"status": "flagged", "due_at": "2026-05-01T00:00:00Z"},
        before={"status": "notFlagged", "start_at": None, "due_at": None},
        after={"status": "flagged", "due_at": "2026-05-01T00:00:00Z"},
    )
    rev = build_reverse_mail_operation(logger, "op-f")
    assert rev.action == "mail.flag"
    assert rev.args["status"] == "notFlagged"


# ---- read reverse ---------------------------------------------------------

def test_reverse_mail_read_flips(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-r", cmd="mail-read",
        drive_id="me", item_id="m1",
        args={"is_read": True},
        before={"is_read": False}, after={"is_read": True},
    )
    rev = build_reverse_mail_operation(logger, "op-r")
    assert rev.action == "mail.read"
    assert rev.args["is_read"] is False


# ---- focus reverse --------------------------------------------------------

def test_reverse_mail_focus_restores_prior(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-fo", cmd="mail-focus",
        drive_id="me", item_id="m1",
        args={"inference_classification": "focused"},
        before={"inference_classification": "other"},
        after={"inference_classification": "focused"},
    )
    rev = build_reverse_mail_operation(logger, "op-fo")
    assert rev.action == "mail.focus"
    assert rev.args["inference_classification"] == "other"


# ---- categorize reverse ---------------------------------------------------

def test_reverse_mail_categorize_restores_prior_list(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-cat", cmd="mail-categorize",
        drive_id="me", item_id="m1",
        args={"categories": ["Followup", "Waiting"]},
        before={"categories": ["Archived"]},
        after={"categories": ["Followup", "Waiting"]},
    )
    rev = build_reverse_mail_operation(logger, "op-cat")
    assert rev.action == "mail.categorize"
    assert rev.args["categories"] == ["Archived"]


# ---- Dispatcher registration ----------------------------------------------

def test_register_mail_inverses_includes_phase3_verbs():
    d = Dispatcher()
    register_mail_inverses(d)
    for action in (
        "mail.move", "mail.copy", "mail.flag", "mail.read",
        "mail.focus", "mail.categorize",
    ):
        assert d.is_registered(action), f"missing {action}"
    # mail.delete.soft is registered (Phase 3 wires the preflight entry);
    # the executor itself lives in Phase 4.
    assert d.is_registered("mail.delete.soft")
```

- [ ] **Step 2: Extend `build_reverse_mail_operation` in `src/m365ctl/mail/mutate/undo.py`.**

Add new branches after the existing `mail-categories-*` block, matching the cmd strings emitted by the Phase 3 executors (`mail-move`, `mail-copy`, `mail-flag`, `mail-read`, `mail-focus`, `mail-categorize`). Use the existing reversal-pattern style:

```python
    if cmd == "mail-move":
        prior_parent = before.get("parent_folder_id")
        if not prior_parent:
            raise Irreversible(
                f"mail-move op {op_id!r} has no before.parent_folder_id; cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.move",
            drive_id=drive_id, item_id=start["item_id"],
            args={"destination_id": prior_parent,
                  "destination_path": before.get("parent_folder_path", "")},
            dry_run_result=f"(undo of {op_id}) move back to "
                           f"{before.get('parent_folder_path', prior_parent)!r}",
        )

    if cmd == "mail-copy":
        new_id = after.get("new_message_id")
        if not new_id:
            raise Irreversible(
                f"mail-copy op {op_id!r} has no after.new_message_id; cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.delete.soft",
            drive_id=drive_id, item_id=new_id, args={},
            dry_run_result=f"(undo of {op_id}) soft-delete the copy {new_id!r}",
        )

    if cmd == "mail-flag":
        return Operation(
            op_id=new_op_id(), action="mail.flag",
            drive_id=drive_id, item_id=start["item_id"],
            args={"status": before.get("status", "notFlagged"),
                  "start_at": before.get("start_at"),
                  "due_at": before.get("due_at")},
            dry_run_result=f"(undo of {op_id}) restore flag "
                           f"{before.get('status', 'notFlagged')!r}",
        )

    if cmd == "mail-read":
        return Operation(
            op_id=new_op_id(), action="mail.read",
            drive_id=drive_id, item_id=start["item_id"],
            args={"is_read": bool(before.get("is_read", False))},
            dry_run_result=f"(undo of {op_id}) set is_read back to "
                           f"{before.get('is_read', False)}",
        )

    if cmd == "mail-focus":
        return Operation(
            op_id=new_op_id(), action="mail.focus",
            drive_id=drive_id, item_id=start["item_id"],
            args={"inference_classification":
                  before.get("inference_classification", "focused")},
            dry_run_result=f"(undo of {op_id}) restore focus "
                           f"{before.get('inference_classification', '?')!r}",
        )

    if cmd == "mail-categorize":
        return Operation(
            op_id=new_op_id(), action="mail.categorize",
            drive_id=drive_id, item_id=start["item_id"],
            args={"categories": list(before.get("categories", []))},
            dry_run_result=f"(undo of {op_id}) restore categories "
                           f"{before.get('categories', [])}",
        )
```

- [ ] **Step 3: Register 6 new Dispatcher inverses.**

In the `register_mail_inverses` function at the bottom of the file, add:

```python
    dispatcher.register("mail.move",       lambda b, a: {"action": "mail.move",
                                                         "args": {"destination_id": b.get("parent_folder_id", "")}})
    dispatcher.register("mail.copy",       lambda b, a: {"action": "mail.delete.soft",
                                                         "args": {}})
    dispatcher.register("mail.flag",       lambda b, a: {"action": "mail.flag",
                                                         "args": {"status": b.get("status", "notFlagged"),
                                                                  "start_at": b.get("start_at"),
                                                                  "due_at": b.get("due_at")}})
    dispatcher.register("mail.read",       lambda b, a: {"action": "mail.read",
                                                         "args": {"is_read": bool(b.get("is_read", False))}})
    dispatcher.register("mail.focus",      lambda b, a: {"action": "mail.focus",
                                                         "args": {"inference_classification":
                                                                  b.get("inference_classification", "focused")}})
    dispatcher.register("mail.categorize", lambda b, a: {"action": "mail.categorize",
                                                         "args": {"categories": list(b.get("categories", []))}})
    # Phase 4 lands the actual execute_soft_delete; Phase 3 registers only so
    # preflight (is_registered) returns True and the mail-undo CLI routes.
    dispatcher.register("mail.delete.soft", lambda b, a: {"action": "mail.delete.soft", "args": {}})
```

- [ ] **Step 4: Extend `src/m365ctl/mail/cli/undo.py` with new dispatch branches.**

Add executor branches for the 6 new actions. For `mail.delete.soft`, print a Phase 4 deferral message and return 2.

Read the existing `run_undo_mail` function. After the `mail.categories.remove` branch, add:

```python
    elif action == "mail.move":
        from m365ctl.mail.mutate.move import execute_move
        rev.args.setdefault("auth_mode", auth_mode)
        # before capture: fetch current parent to make the undo's undo possible
        try:
            from m365ctl.mail.messages import get_message
            msg = get_message(graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode, message_id=rev.item_id)
            current_before = {"parent_folder_id": msg.parent_folder_id,
                              "parent_folder_path": msg.parent_folder_path}
        except Exception:
            current_before = {}
        r = execute_move(rev, graph, logger, before=current_before)

    elif action == "mail.delete.soft":
        print(
            f"mail undo: cmd for this op was mail-copy; inverse is mail.delete.soft "
            f"on the copy {rev.item_id!r}. Phase 4 adds programmatic delete-soft; "
            f"for now remove the copy via Outlook UI.",
            file=sys.stderr,
        )
        return 2

    elif action == "mail.flag":
        from m365ctl.mail.mutate.flag import execute_flag
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_flag(rev, graph, logger, before={})

    elif action == "mail.read":
        from m365ctl.mail.mutate.read import execute_read
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_read(rev, graph, logger, before={})

    elif action == "mail.focus":
        from m365ctl.mail.mutate.focus import execute_focus
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_focus(rev, graph, logger, before={})

    elif action == "mail.categorize":
        from m365ctl.mail.mutate.categorize import execute_categorize
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_categorize(rev, graph, logger, before={})
```

- [ ] **Step 5: Run + commit.**
```bash
uv run pytest tests/test_mail_mutate_undo_phase3.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/mutate/undo.py src/m365ctl/mail/cli/undo.py tests/test_mail_mutate_undo_phase3.py
git commit -m "feat(mail/mutate): Phase 3 undo — reverse-op builders + Dispatcher + CLI routes for 6 new verbs"
```

Expected suite: 434 + 9 = 443.

---

## Group 7: CLI — mail move + mail copy

### Task 7: `mail/cli/move.py` + `mail/cli/copy.py`

**Files:**
- Modify (replace stub): `src/m365ctl/mail/cli/move.py` (stub from Phase 1)
- Modify (replace stub): `src/m365ctl/mail/cli/copy.py` (stub from Phase 1)
- Modify: `src/m365ctl/mail/cli/__main__.py` — ensure `move` + `copy` verbs route to the new modules (already do — they route to the stubs).
- Create: `tests/test_cli_mail_move.py`
- Create: `tests/test_cli_mail_copy.py`

Each CLI accepts:
- **Single item**: `--message-id <id> --to-folder <path>` (+ `--confirm`).
- **Pattern bulk**: `--from <addr>`, `--subject <sub>`, `--folder <path>`, `--unread`, etc. (filters) + `--to-folder <path>` + `--plan-out <file>` OR `--from-plan <file> --confirm`.

Both always pass `--mailbox` and the standard `add_common_args` surface from `_common.py`.

- [ ] **Step 1: Failing parser tests for `mail move`.**

Write `tests/test_cli_mail_move.py`:
```python
import pytest

from m365ctl.mail.cli.move import build_parser


def test_move_parser_single_mode():
    args = build_parser().parse_args([
        "--message-id", "m1",
        "--to-folder", "/Archive",
        "--confirm",
    ])
    assert args.message_id == "m1"
    assert args.to_folder == "/Archive"
    assert args.confirm is True


def test_move_parser_bulk_plan_out():
    args = build_parser().parse_args([
        "--from", "alice@example.com",
        "--subject", "old",
        "--folder", "/Inbox",
        "--to-folder", "/Archive/Old",
        "--plan-out", "/tmp/p.json",
    ])
    assert args.from_address == "alice@example.com"
    assert args.subject_contains == "old"
    assert args.folder == "/Inbox"
    assert args.to_folder == "/Archive/Old"
    assert args.plan_out == "/tmp/p.json"
    assert args.confirm is False


def test_move_parser_from_plan_requires_confirm():
    args = build_parser().parse_args([
        "--from-plan", "/tmp/p.json",
        "--confirm",
    ])
    assert args.from_plan == "/tmp/p.json"
    assert args.confirm is True


def test_move_parser_no_args_still_valid():
    # Validation of required args happens in main(), not the parser.
    args = build_parser().parse_args([])
    assert args.message_id is None
    assert args.from_plan is None
```

- [ ] **Step 2: Implement `src/m365ctl/mail/cli/move.py`.**

```python
"""`m365ctl mail move` — move one or more messages to a destination folder.

Three modes:
1. Single-item: `--message-id <id> --to-folder <path>` + `--confirm`.
2. Bulk dry-run: filter flags + `--to-folder <path>` + `--plan-out <file>`.
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
from m365ctl.mail.mutate.move import execute_move


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail move")
    add_common_args(p)

    # Mode 1: single-item
    p.add_argument("--message-id", help="Move one specific message.")

    # Mode 2: bulk pattern — filters inherited from mail list
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

    # Destination + plan plumbing
    p.add_argument("--to-folder", help="Destination folder path.")
    p.add_argument("--plan-out", help="Write plan to this path and exit (dry run).")
    p.add_argument("--from-plan", help="Execute ops from this plan file (requires --confirm).")

    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--page-size", type=int, default=50)
    return p


def _build_filter(args) -> MessageFilter:
    unread_flag: bool | None = None
    if args.unread and args.read:
        # Caller sanity check — also validated in main().
        return MessageFilter()
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
            print("mail move --from-plan requires --confirm.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        plan = load_plan(Path(args.from_plan))
        ops = [op for op in plan.operations if op.action == "mail.move"]
        if not ops:
            print("mail move --from-plan: no mail.move ops in plan.", file=sys.stderr)
            return 2
        if not confirm_bulk_proceed(len(ops), verb="move"):
            print("aborted: user declined /dev/tty confirm.", file=sys.stderr)
            return 2
        token = cred.get_token()
        graph = GraphClient(token_provider=lambda: token)
        logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
        any_error = False
        for op in ops:
            op.args.setdefault("auth_mode", auth_mode)
            # Look up current parent for before capture.
            try:
                msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                                  message_id=op.item_id)
                before = {"parent_folder_id": msg.parent_folder_id,
                          "parent_folder_path": msg.parent_folder_path}
            except Exception:
                before = {}
            result = execute_move(op, graph, logger, before=before)
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    # --- Single-item mode ---------------------------------------------------
    if args.message_id:
        if not args.to_folder:
            print("mail move --message-id requires --to-folder.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        assert_mail_target_allowed(
            cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
            unsafe_scope=args.unsafe_scope, folder_path=args.to_folder,
        )
        if not args.confirm:
            print(f"(dry-run) would move {args.message_id} -> {args.to_folder!r}",
                  file=sys.stderr)
            return 0
        token = cred.get_token()
        graph = GraphClient(token_provider=lambda: token)
        try:
            dest_id = resolve_folder_path(
                args.to_folder, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
            )
        except FolderNotFound as e:
            print(f"mail move: {e}", file=sys.stderr)
            return 2
        try:
            msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                              message_id=args.message_id)
            before = {"parent_folder_id": msg.parent_folder_id,
                      "parent_folder_path": msg.parent_folder_path}
        except Exception:
            before = {}
        op = Operation(
            op_id=new_op_id(), action="mail.move",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
            args={"destination_id": dest_id, "destination_path": args.to_folder,
                  "auth_mode": auth_mode},
        )
        logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
        result = execute_move(op, graph, logger, before=before)
        if result.status != "ok":
            print(f"error: {result.error}", file=sys.stderr)
            return 1
        print(f"[{op.op_id}] ok — moved {args.message_id} -> {args.to_folder!r}")
        return 0

    # --- Bulk plan-out mode -------------------------------------------------
    if not args.to_folder:
        print("mail move: pass --message-id, --from-plan, or filter flags with --to-folder.",
              file=sys.stderr)
        return 2
    if args.unread and args.read:
        print("mail move: --unread and --read are mutually exclusive", file=sys.stderr)
        return 2

    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope, folder_path=args.to_folder,
    )
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        source_folder_id = resolve_folder_path(
            args.folder, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
        dest_id = resolve_folder_path(
            args.to_folder, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
    except FolderNotFound as e:
        print(f"mail move: {e}", file=sys.stderr)
        return 2

    msgs = list(expand_messages_for_pattern(
        graph=graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        resolved_folders=[(source_folder_id, args.folder)],
        filter=_build_filter(args),
        limit=args.limit, page_size=args.page_size,
    ))
    if not msgs:
        print("mail move: no matching messages; nothing to do.")
        return 0

    ops = [
        Operation(
            op_id=new_op_id(), action="mail.move",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=m.id,
            args={"destination_id": dest_id, "destination_path": args.to_folder,
                  "auth_mode": auth_mode},
            dry_run_result=f"would move {m.id} ({m.subject!r}) -> {args.to_folder}",
        )
        for m in msgs
    ]

    if args.plan_out:
        emit_plan(
            Path(args.plan_out),
            source_cmd=f"mail move --from {args.from_address or '?'} --to-folder {args.to_folder}",
            scope=derive_mailbox_upn(args.mailbox),
            operations=ops,
        )
        print(f"Wrote plan with {len(ops)} operations to {args.plan_out}.")
        print(f"Review, then: mail move --from-plan {args.plan_out} --confirm")
        return 0

    # No --plan-out and no --confirm: just print a dry-run summary.
    print(f"mail move: matched {len(msgs)} messages. Pass --plan-out <path> to persist, "
          f"or --confirm to execute inline.")
    for op in ops[:10]:
        print(f"  {op.dry_run_result}")
    if len(ops) > 10:
        print(f"  ... and {len(ops) - 10} more")
    return 0
```

- [ ] **Step 3: `src/m365ctl/mail/cli/copy.py`** — same shape as move.py but calls `execute_copy` and emits `mail.copy` ops. Key differences:
  - Uses `execute_copy` instead of `execute_move`.
  - Action is `"mail.copy"`.
  - `before={}` (copy doesn't need prior parent — it creates a new message).

Write `tests/test_cli_mail_copy.py` with the same 4 parser tests as move (substitute "copy" for "move" in imports/classes).

Implement `src/m365ctl/mail/cli/copy.py` — **copy this file from `move.py` and change these specific strings**:
- `prog="m365ctl mail copy"` (not "move")
- `execute_copy` import + call (not `execute_move`)
- `"mail.copy"` action string (not `"mail.move"`)
- Commit messages and log output mention "copy" not "move"
- `before={}` in both single-item and bulk paths — copy doesn't need prior parent
- `action` filter `op.action == "mail.copy"` (not `"mail.move"`)
- Error message: "mail copy: ..." not "mail move: ..."

Everything else (filter flags, plan-out/from-plan modes, scope checks) is identical.

- [ ] **Step 4: Run + commit.**
```bash
uv run pytest tests/test_cli_mail_move.py tests/test_cli_mail_copy.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/cli/move.py src/m365ctl/mail/cli/copy.py tests/test_cli_mail_move.py tests/test_cli_mail_copy.py
git commit -m "feat(mail/cli): move + copy with single + bulk-plan workflows"
```

Expected suite: 443 + 4 + 4 = 451.

---

## Group 8: CLI — mail flag + read + focus + categorize

### Task 8: Four single-field PATCH CLIs

**Files:**
- Modify (replace stubs): `src/m365ctl/mail/cli/{flag,read,focus,categorize}.py`
- Create: `tests/test_cli_mail_{flag,read,focus,categorize}.py`

These four CLIs are simpler than move/copy because the "bulk pattern" use case is less common. Ship each with single-item mode; bulk-from-plan consumes plans built elsewhere.

Each CLI:
- Single-item: `--message-id <id>` + verb-specific flag (`--status flagged`, `--read`/`--unread`, `--focused`/`--other`, `--set/--add/--remove`).
- `--from-plan <path> --confirm` to execute a plan.

- [ ] **Step 1: Parser tests + implementations.**

For brevity, the plan shows the full implementation + tests for `flag` only. For `read`, `focus`, `categorize`, follow the same template with these specific shapes:

**mail-flag:**
```python
# tests/test_cli_mail_flag.py
import pytest
from m365ctl.mail.cli.flag import build_parser


def test_flag_parser_single_item():
    args = build_parser().parse_args([
        "--message-id", "m1",
        "--status", "flagged",
        "--due", "2026-04-30T17:00:00Z",
        "--confirm",
    ])
    assert args.message_id == "m1"
    assert args.status == "flagged"
    assert args.due == "2026-04-30T17:00:00Z"
    assert args.confirm is True


def test_flag_parser_rejects_invalid_status():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--status", "maybe"])


def test_flag_parser_from_plan():
    args = build_parser().parse_args(["--from-plan", "/tmp/p.json", "--confirm"])
    assert args.from_plan == "/tmp/p.json"
```

```python
# src/m365ctl/mail/cli/flag.py
"""`m365ctl mail flag` — set/clear the flag on one or more messages."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, load_plan, new_op_id
from m365ctl.mail.cli._bulk import confirm_bulk_proceed
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.messages import get_message
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.flag import execute_flag


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail flag")
    add_common_args(p)
    p.add_argument("--message-id")
    p.add_argument("--status", choices=("notFlagged", "flagged", "complete"))
    p.add_argument("--start")
    p.add_argument("--due")
    p.add_argument("--from-plan")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.from_plan:
        if not args.confirm:
            print("mail flag --from-plan requires --confirm.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        plan = load_plan(Path(args.from_plan))
        ops = [op for op in plan.operations if op.action == "mail.flag"]
        if not confirm_bulk_proceed(len(ops), verb="flag"):
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
                before = {
                    "status": msg.flag.status,
                    "start_at": msg.flag.start_at.isoformat() if msg.flag.start_at else None,
                    "due_at": msg.flag.due_at.isoformat() if msg.flag.due_at else None,
                }
            except Exception:
                before = {}
            result = execute_flag(op, graph, logger, before=before)
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    if not args.message_id or not args.status:
        print("mail flag: pass --message-id + --status (or --from-plan --confirm).",
              file=sys.stderr)
        return 2
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        print(f"(dry-run) would flag {args.message_id} status={args.status}",
              file=sys.stderr)
        return 0
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                          message_id=args.message_id)
        before = {
            "status": msg.flag.status,
            "start_at": msg.flag.start_at.isoformat() if msg.flag.start_at else None,
            "due_at": msg.flag.due_at.isoformat() if msg.flag.due_at else None,
        }
    except Exception:
        before = {}
    op = Operation(
        op_id=new_op_id(), action="mail.flag",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
        args={"status": args.status,
              "start_at": args.start,
              "due_at": args.due,
              "auth_mode": auth_mode},
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_flag(op, graph, logger, before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — flagged {args.message_id} status={args.status}")
    return 0
```

**mail-read:** Verb-specific flag: `--yes` (sets isRead True) / `--no` (sets isRead False). Required: exactly one of `--yes`/`--no`. Parser tests mirror `flag` minus status/start/due.

```python
# src/m365ctl/mail/cli/read.py
"""`m365ctl mail read` — mark message read/unread."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, load_plan, new_op_id
from m365ctl.mail.cli._bulk import confirm_bulk_proceed
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.messages import get_message
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.read import execute_read


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail read")
    add_common_args(p)
    p.add_argument("--message-id")
    p.add_argument("--yes", dest="set_read", action="store_const", const=True,
                   help="Mark message as read.")
    p.add_argument("--no", dest="set_read", action="store_const", const=False,
                   help="Mark message as unread.")
    p.add_argument("--from-plan")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.from_plan:
        if not args.confirm:
            print("mail read --from-plan requires --confirm.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        plan = load_plan(Path(args.from_plan))
        ops = [op for op in plan.operations if op.action == "mail.read"]
        if not confirm_bulk_proceed(len(ops), verb="read"):
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
                before = {"is_read": msg.is_read}
            except Exception:
                before = {}
            result = execute_read(op, graph, logger, before=before)
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    if not args.message_id or args.set_read is None:
        print("mail read: pass --message-id + --yes or --no (or --from-plan --confirm).",
              file=sys.stderr)
        return 2
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        print(f"(dry-run) would set is_read={args.set_read} on {args.message_id}",
              file=sys.stderr)
        return 0
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                          message_id=args.message_id)
        before = {"is_read": msg.is_read}
    except Exception:
        before = {}
    op = Operation(
        op_id=new_op_id(), action="mail.read",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
        args={"is_read": args.set_read, "auth_mode": auth_mode},
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_read(op, graph, logger, before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    state = "read" if args.set_read else "unread"
    print(f"[{op.op_id}] ok — marked {args.message_id} as {state}")
    return 0
```

```python
# tests/test_cli_mail_read.py
import pytest
from m365ctl.mail.cli.read import build_parser


def test_read_parser_yes():
    args = build_parser().parse_args(["--message-id", "m1", "--yes", "--confirm"])
    assert args.set_read is True


def test_read_parser_no():
    args = build_parser().parse_args(["--message-id", "m1", "--no", "--confirm"])
    assert args.set_read is False


def test_read_parser_from_plan():
    args = build_parser().parse_args(["--from-plan", "/tmp/p.json", "--confirm"])
    assert args.from_plan == "/tmp/p.json"
```

**mail-focus:** verb-specific flag: `--focused` / `--other`. Same parser pattern as `read`.

```python
# src/m365ctl/mail/cli/focus.py
"""`m365ctl mail focus` — set inferenceClassification (focused | other)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, load_plan, new_op_id
from m365ctl.mail.cli._bulk import confirm_bulk_proceed
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.messages import get_message
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.focus import execute_focus


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail focus")
    add_common_args(p)
    p.add_argument("--message-id")
    p.add_argument("--focused", dest="classification",
                   action="store_const", const="focused")
    p.add_argument("--other", dest="classification",
                   action="store_const", const="other")
    p.add_argument("--from-plan")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.from_plan:
        if not args.confirm:
            print("mail focus --from-plan requires --confirm.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        plan = load_plan(Path(args.from_plan))
        ops = [op for op in plan.operations if op.action == "mail.focus"]
        if not confirm_bulk_proceed(len(ops), verb="focus"):
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
                before = {"inference_classification": msg.inference_classification}
            except Exception:
                before = {}
            result = execute_focus(op, graph, logger, before=before)
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    if not args.message_id or args.classification is None:
        print("mail focus: pass --message-id + --focused or --other (or --from-plan --confirm).",
              file=sys.stderr)
        return 2
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        print(f"(dry-run) would set focus={args.classification} on {args.message_id}",
              file=sys.stderr)
        return 0
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                          message_id=args.message_id)
        before = {"inference_classification": msg.inference_classification}
    except Exception:
        before = {}
    op = Operation(
        op_id=new_op_id(), action="mail.focus",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
        args={"inference_classification": args.classification, "auth_mode": auth_mode},
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_focus(op, graph, logger, before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — set focus={args.classification} on {args.message_id}")
    return 0
```

```python
# tests/test_cli_mail_focus.py
import pytest
from m365ctl.mail.cli.focus import build_parser


def test_focus_parser_focused():
    args = build_parser().parse_args(["--message-id", "m1", "--focused", "--confirm"])
    assert args.classification == "focused"


def test_focus_parser_other():
    args = build_parser().parse_args(["--message-id", "m1", "--other", "--confirm"])
    assert args.classification == "other"


def test_focus_parser_from_plan():
    args = build_parser().parse_args(["--from-plan", "/tmp/p.json", "--confirm"])
    assert args.from_plan == "/tmp/p.json"
```

**mail-categorize:** Verb-specific flags: `--add X`, `--remove X`, `--set X`. Can be repeated. CLI resolves to a final list before calling executor.

```python
# src/m365ctl/mail/cli/categorize.py
"""`m365ctl mail categorize` — add/remove/set categories on a message."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, load_plan, new_op_id
from m365ctl.mail.cli._bulk import confirm_bulk_proceed
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.messages import get_message
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.categorize import execute_categorize


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail categorize")
    add_common_args(p)
    p.add_argument("--message-id")
    p.add_argument("--add", action="append", default=[],
                   help="Add category. Repeatable.")
    p.add_argument("--remove", action="append", default=[],
                   help="Remove category. Repeatable.")
    p.add_argument("--set", dest="set_", action="append", default=[],
                   help="Set exact category list. Repeatable. Mutually exclusive with add/remove.")
    p.add_argument("--from-plan")
    return p


def _resolve_final_categories(current: list[str], add: list[str], remove: list[str], set_: list[str]) -> list[str]:
    if set_:
        return list(set_)
    out = list(current)
    for c in add:
        if c not in out:
            out.append(c)
    for c in remove:
        if c in out:
            out.remove(c)
    return out


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.from_plan:
        if not args.confirm:
            print("mail categorize --from-plan requires --confirm.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        plan = load_plan(Path(args.from_plan))
        ops = [op for op in plan.operations if op.action == "mail.categorize"]
        if not confirm_bulk_proceed(len(ops), verb="categorize"):
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
                before = {"categories": list(msg.categories)}
            except Exception:
                before = {}
            result = execute_categorize(op, graph, logger, before=before)
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    if not args.message_id:
        print("mail categorize: pass --message-id (or --from-plan --confirm).",
              file=sys.stderr)
        return 2
    if args.set_ and (args.add or args.remove):
        print("mail categorize: --set is mutually exclusive with --add/--remove.",
              file=sys.stderr)
        return 2
    if not (args.set_ or args.add or args.remove):
        print("mail categorize: pass --set, --add, or --remove.", file=sys.stderr)
        return 2

    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        print(f"(dry-run) would categorize {args.message_id}: set={args.set_}, add={args.add}, remove={args.remove}",
              file=sys.stderr)
        return 0
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                          message_id=args.message_id)
        current = list(msg.categories)
        before = {"categories": current}
    except Exception:
        current = []
        before = {}

    final = _resolve_final_categories(current, args.add, args.remove, args.set_)
    op = Operation(
        op_id=new_op_id(), action="mail.categorize",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
        args={"categories": final, "auth_mode": auth_mode},
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_categorize(op, graph, logger, before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — categorized {args.message_id} {final}")
    return 0
```

```python
# tests/test_cli_mail_categorize.py
import pytest
from m365ctl.mail.cli.categorize import _resolve_final_categories, build_parser


def test_categorize_parser_add():
    args = build_parser().parse_args(["--message-id", "m1", "--add", "X", "--confirm"])
    assert args.add == ["X"]


def test_categorize_parser_set_repeated():
    args = build_parser().parse_args(["--message-id", "m1", "--set", "X", "--set", "Y"])
    assert args.set_ == ["X", "Y"]


def test_categorize_parser_from_plan():
    args = build_parser().parse_args(["--from-plan", "/tmp/p.json", "--confirm"])
    assert args.from_plan == "/tmp/p.json"


def test_resolve_final_set_replaces():
    out = _resolve_final_categories(["A", "B"], [], [], ["X", "Y"])
    assert out == ["X", "Y"]


def test_resolve_final_add_removes_dedup():
    out = _resolve_final_categories(["A"], ["B", "A"], [], [])
    assert out == ["A", "B"]


def test_resolve_final_remove():
    out = _resolve_final_categories(["A", "B", "C"], [], ["B"], [])
    assert out == ["A", "C"]
```

- [ ] **Step 2: Run + commit each CLI.** Four separate commits is fine; or bundle into one `feat(mail/cli): flag + read + focus + categorize verbs`:

```bash
uv run pytest tests/test_cli_mail_flag.py tests/test_cli_mail_read.py tests/test_cli_mail_focus.py tests/test_cli_mail_categorize.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/cli/flag.py src/m365ctl/mail/cli/read.py src/m365ctl/mail/cli/focus.py src/m365ctl/mail/cli/categorize.py \
         tests/test_cli_mail_flag.py tests/test_cli_mail_read.py tests/test_cli_mail_focus.py tests/test_cli_mail_categorize.py
git commit -m "feat(mail/cli): flag + read + focus + categorize CLIs with single + from-plan modes"
```

Expected suite: 451 + 3 + 3 + 3 + 6 = 466.

---

## Group 9: `__main__` routing + bin wrappers

### Task 9: Wire the 6 new verbs into the mail sub-dispatcher + create bin wrappers

**Files:**
- Modify: `src/m365ctl/mail/cli/__main__.py` (already has routes for `move/copy/flag/read/focus/categorize` — they currently route to the Phase 1 stubs which Groups 7–8 replaced).
- Create: `bin/mail-{move,copy,flag,read,focus,categorize}` (new wrappers).

- [ ] **Step 1: Verify the dispatcher already imports correctly.**

Run: `uv run python -m m365ctl mail move --help` → should exit 0, print the new parser.

If any verb prints the Phase-1 stub message, the dispatcher's lazy import is wrong — inspect `src/m365ctl/mail/cli/__main__.py` and confirm each branch uses `from m365ctl.mail.cli.<verb> import main as f`. Previous groups ensured this.

- [ ] **Step 2: Create 6 new bin wrappers.**

Each follows the existing `bin/mail-list` shape:
```bash
#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec uv run --project "$REPO" python -m m365ctl mail VERB "$@"
```

Create:
- `bin/mail-move`
- `bin/mail-copy`
- `bin/mail-flag`
- `bin/mail-read`
- `bin/mail-focus`
- `bin/mail-categorize`

Each with `VERB` set accordingly. `chmod +x bin/mail-{move,copy,flag,read,focus,categorize}`.

- [ ] **Step 3: Smoke each.**

```bash
./bin/mail-move --help
./bin/mail-copy --help
./bin/mail-flag --help
./bin/mail-read --help
./bin/mail-focus --help
./bin/mail-categorize --help
```

All exit 0.

- [ ] **Step 4: Commit.**
```bash
git add bin/mail-move bin/mail-copy bin/mail-flag bin/mail-read bin/mail-focus bin/mail-categorize
git commit -m "feat(bin): add mail-{move,copy,flag,read,focus,categorize} wrappers"
```

---

## Group 10: Release 0.4.0 + CHANGELOG + push/PR/merge

### Task 10: Bump + CHANGELOG + final gates

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`
- Create: plan commit `docs(plans): commit Phase 3 plan`

- [ ] **Step 1: Bump version** `0.3.0` → `0.4.0` in `pyproject.toml`.

- [ ] **Step 2: Add `[0.4.0]` CHANGELOG entry** above `[0.3.0]`:

```markdown
## [0.4.0] — 2026-04-24

### Added
- **Safe message mutations (Phase 3).**
  - `m365ctl mail move` — single-item (`--message-id`) or bulk-plan (`--from <addr> --subject <sub> --folder <path> --plan-out` → review → `--from-plan --confirm`).
  - `m365ctl mail copy` — same shape as move; creates a new message in the destination folder.
  - `m365ctl mail flag` — `--status flagged|notFlagged|complete` with optional `--start`/`--due`.
  - `m365ctl mail read` — `--yes` / `--no` toggles `isRead`.
  - `m365ctl mail focus` — `--focused` / `--other` sets inferenceClassification.
  - `m365ctl mail categorize` — `--add X` / `--remove X` / `--set X [--set Y]` with add/remove on current categories or set-exact semantics.
- **First mail-side plan-file workflow:** `--pattern`-style filters → `--plan-out plan.json` → `--from-plan plan.json --confirm`. Bulk ≥20 items require interactive `/dev/tty` confirm (non-bypassable by piped stdin).
- **All Phase 3 verbs are undoable** via `m365ctl undo <op-id>`:
  - `mail.move` ↔ move back to prior parent
  - `mail.flag` ↔ restore prior flag status/start/due
  - `mail.read` ↔ flip `isRead`
  - `mail.focus` ↔ restore prior inferenceClassification
  - `mail.categorize` ↔ restore prior category list
  - `mail.copy` ↔ `mail.delete.soft` on the new message id — **inverse executor lands Phase 4**. For now, the undo CLI prints the new message id and a pointer to Phase 4.
- `GraphClient.patch` + `GraphClient.post` now accept optional `headers={}` for `If-Match: <change_key>` (ETag) plumbing. Executors pass it when `op.args["change_key"]` is set.
- `src/m365ctl/mail/cli/_bulk.py` — `MessageFilter`, `expand_messages_for_pattern`, `emit_plan`, `confirm_bulk_proceed`.
- `bin/mail-move`, `bin/mail-copy`, `bin/mail-flag`, `bin/mail-read`, `bin/mail-focus`, `bin/mail-categorize` short wrappers.

### Safety
- `--confirm` required for every mutation; dry-run default. Bulk plan-out is the intended workflow (review before execute).
- `assert_mail_target_allowed` runs before credential construction and Graph.
- `/dev/tty` confirm on ≥20-item plans — not bypassable by piped stdin.

### Deferred
- `mail.delete.soft` inverse executor → Phase 4 (first mail-message delete verb).
- Interactive ETag retry on 412 → Phase 3.5 or Phase 4 (currently single attempt; 412 surfaces as a GraphError).
```

- [ ] **Step 3: Commit release.**
```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore(release): bump to 0.4.0 + CHANGELOG entry for safe message mutations"
```

- [ ] **Step 4: Commit plan file.**
```bash
git add docs/superpowers/plans/2026-04-24-phase-3-mail-safe-message-mutations.md
git commit -m "docs(plans): commit Phase 3 safe-message-mutations plan"
```

### Task 11: Final gates + push/PR/merge

- [ ] **Step 1: Tests.**
```bash
uv run pytest -m "not live" -q 2>&1 | tail -3
```
Expected: ~466 passed.

- [ ] **Step 2: Ruff.**
```bash
uv run ruff check 2>&1 | tail -5
```
Must be clean. Auto-fix if needed and commit as `fix(lint): ...`.

- [ ] **Step 3: Mypy.**
```bash
uv run mypy src 2>&1 | tail -10
```
Baseline Phase 2: 52 errors. Phase 3 adds ~7 new mutate modules + 6 new CLIs; expect modest growth (<15). If >15 new errors, triage the top offenders.

- [ ] **Step 4: CLI smokes.**
```bash
uv run python -m m365ctl mail --help
./bin/mail-move --help
./bin/mail-copy --help
./bin/mail-flag --help
./bin/mail-read --help
./bin/mail-focus --help
./bin/mail-categorize --help
uv run python -m m365ctl undo --help
```
All exit 0.

- [ ] **Step 5: Push + PR + merge.**
```bash
git push -u origin phase-3-mail-safe-message-mutations
gh pr create --title "Phase 3: safe message mutations + first mail plan-file workflow (0.4.0)" --body "..."
gh pr checks <N> --watch
gh pr merge <N> --merge --delete-branch
git checkout main && git pull
```

### User-performed live-tenant smoke

```bash
# Single-item
./bin/mail-move --message-id <id> --to-folder /Archive --confirm
./bin/m365ctl-undo <op-id>                    # reverses the move

./bin/mail-flag --message-id <id> --status flagged --due 2026-04-30T17:00:00Z --confirm
./bin/m365ctl-undo <op-id>                    # restores prior flag state

./bin/mail-categorize --message-id <id> --add Followup --confirm
./bin/m365ctl-undo <op-id>                    # restores prior category list

# Bulk plan
./bin/mail-move --from noisy-sender@example.com --folder Inbox --to-folder /Archive/Noise --plan-out plan.json
cat plan.json | head -20
./bin/mail-move --from-plan plan.json --confirm
```

---

## Self-review

**1. Spec coverage (spec §19 Phase 3 deliverables):**
- [x] `mail.mutate.{move, flag, categorize}` — Tasks 3, 4, 5.
- [x] `mail.mutate.copy` — Task 3.
- [x] CLI verbs (`mail-move`, `mail-copy`, `mail-flag`, `mail-read`, `mail-focus`, `mail-categorize`) — Tasks 7, 8.
- [x] Plan-file bulk via `--pattern`/`--plan-out`/`--from-plan --confirm` — Tasks 7, 8 (move + copy get full bulk; flag/read/focus/categorize support `--from-plan`).
- [x] `mail.cli._common` pattern expansion — Task 2 (landed as `_bulk.py` to keep responsibility focused).
- [x] Interactive confirm on >N items — Task 2 (`confirm_bulk_proceed` with threshold 20).
- [x] Bump to 0.4.0 — Task 10.

**2. Acceptance (spec §19 Phase 3):**
- Single-item + bulk workflows — covered per verb.
- `m365ctl undo` restores prior state — Task 6 wires all 6 inverses + CLI dispatch; test file `tests/test_mail_mutate_undo_phase3.py` has 9 assertions.
- ETag mismatch retry — **partially deferred**. Phase 3 lands `If-Match` header plumbing (executors accept `change_key`); the automatic 412→refresh→retry loop is noted as "deferred to Phase 3.5/4" in the CHANGELOG. Acceptance says "single retry"; in Phase 3 the 412 surfaces as a GraphError and the user re-runs. This is a real acceptance gap — documented explicitly.
- Scope-violation test per verb — not every CLI has one (the pattern from `tests/test_mail_safety.py::test_mail_list_fails_fast_when_mailbox_not_in_allow_list` covers the load-and-authorize path; mutation CLIs inherit the same pre-auth check via `assert_mail_target_allowed`). Explicit per-verb e2e tests could be added but would be repetitive; leaving as follow-up.

**3. Placeholder scan:** No "TBD" / "implement later" outside the explicitly-deferred items (ETag retry, delete-soft executor).

**4. Type consistency:**
- `execute_<verb>(op, graph, logger, *, before) -> MailResult` — consistent across all 6 executors.
- `op.args["auth_mode"]` — consistent default "delegated".
- `MessageFilter.as_list_filters() -> MessageListFilters` — type-aligned with `mail.messages.list_messages` signature.
- `_resolve_final_categories(current, add, remove, set_)` — only used inside `categorize.py`, consistent.
- `op.args["change_key"]` → `If-Match` header — consistent across flag/read/focus/categorize; move/copy don't thread ETag currently (Graph's move/copy don't honor ETag).

---

Plan complete.
