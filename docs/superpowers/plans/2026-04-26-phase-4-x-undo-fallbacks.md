# Phase 4.x — Soft-Delete-Undo Fallback Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Tighten the soft-delete-undo recovery path for two real-world cases the current code doesn't handle:

1. **Manually-moved-out-of-Deleted-Items**: the user soft-deleted a message via `mail delete`, then opened Outlook and dragged it from Deleted Items to (e.g.) Archive. The undo's id-and-internetMessageId lookup against `deleteditems` returns None and the user gets a "manually restore" error even though we could find it.
2. **Already restored**: same scenario but the user dragged it back to the original folder. Undo currently emits a no-op move-to-self that fails or duplicates the message; should detect "already there" and exit cleanly.

**Approach:**
- Add a broader `find_message_anywhere(graph, *, mailbox_spec, auth_mode, internet_message_id) -> tuple[str, str] | None` helper in `mail/messages.py` that searches the whole mailbox via `/{ub}/messages?$filter=internetMessageId eq '...'&$top=1&$select=id,parentFolderId`. Returns `(message_id, parent_folder_id)` or None.
- In `mail/cli/undo.py:_run_undo` (mail.move branch), when the existing `find_by_internet_message_id` against `deleteditems` returns None, fall back to `find_message_anywhere`. If found:
  - If the message's current `parent_folder_id == rev.args["to_folder_id"]` (the restore target), print `"undo: message already in original folder; nothing to do"` and exit 0.
  - Otherwise, print `"undo: message manually moved to <folder_id>; restoring from there"`, patch `rev.item_id` and proceed with the move.

**Tech stack:** Existing primitives. No schema change.

**Baseline:** `main` post-PR-#27 (95d1523), 947 passing tests, 0 mypy errors, ruff clean. Tag `v1.10.0`.

**Version bump:** 1.10.0 → 1.11.0.

---

## Group 1 — Helper + undo wiring (one commit)

**Files:**
- Modify: `src/m365ctl/mail/messages.py` — add `find_message_anywhere`.
- Modify: `src/m365ctl/mail/cli/undo.py` — broaden the mail.move recovery path.
- Modify: `tests/test_mail_messages.py` — tests for the new helper.
- Modify: `tests/test_mail_cli_undo_recovery.py` (or wherever the existing Phase 4.x tests live) — tests for the broader fallback.

### Steps

- [ ] **Step 1: Failing tests** in `tests/test_mail_messages.py`:

```python
def test_find_message_anywhere_returns_id_and_parent_on_hit():
    graph = MagicMock()
    graph.get.return_value = {
        "value": [{"id": "rotated-id-9", "parentFolderId": "fld-archive"}]
    }
    out = find_message_anywhere(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        internet_message_id="<abc@example.com>",
    )
    assert out == ("rotated-id-9", "fld-archive")
    call_args = graph.get.call_args
    assert call_args.args[0] == "/me/messages"
    params = call_args.kwargs["params"]
    assert params["$filter"] == "internetMessageId eq '<abc@example.com>'"
    assert params["$top"] == 1
    assert params["$select"] == "id,parentFolderId"


def test_find_message_anywhere_returns_none_on_miss():
    graph = MagicMock()
    graph.get.return_value = {"value": []}
    out = find_message_anywhere(
        graph, mailbox_spec="me", auth_mode="delegated",
        internet_message_id="<missing@example.com>",
    )
    assert out is None


def test_find_message_anywhere_app_only_routes_via_users_upn():
    graph = MagicMock()
    graph.get.return_value = {"value": [{"id": "x", "parentFolderId": "f"}]}
    find_message_anywhere(
        graph, mailbox_spec="upn:bob@example.com", auth_mode="app-only",
        internet_message_id="<abc@example.com>",
    )
    assert graph.get.call_args.args[0] == "/users/bob@example.com/messages"


def test_find_message_anywhere_escapes_single_quote():
    graph = MagicMock()
    graph.get.return_value = {"value": []}
    find_message_anywhere(
        graph, mailbox_spec="me", auth_mode="delegated",
        internet_message_id="<O'Brien@example.com>",
    )
    params = graph.get.call_args.kwargs["params"]
    assert params["$filter"] == "internetMessageId eq '<O''Brien@example.com>'"
```

In `tests/test_mail_cli_undo_recovery.py` (extend the existing file):

```python
def test_undo_falls_back_to_anywhere_search_when_not_in_deleted_items(
    tmp_path, capsys
):
    """When DeletedItems lookup misses, search the whole mailbox."""
    # ... setup an audit record for a soft-delete with a recorded
    # internet_message_id ...
    # ... mock get_message to raise GraphError(404) (recorded id is stale)
    # ... mock find_by_internet_message_id (DeletedItems-scoped) to return None
    # ... mock find_message_anywhere to return ("new-id", "fld-archive")
    # ... mock execute_move
    # Expected: stderr message names "fld-archive"; execute_move called with
    # the new id; exit 0 on success.


def test_undo_no_op_when_message_already_in_target_folder(tmp_path, capsys):
    """User manually moved the message back; undo should exit cleanly."""
    # ... same setup as above but find_message_anywhere returns
    # ("new-id", <original-folder-id>) — i.e., the message is already where
    # the undo wants to put it.
    # Expected: stderr "already in original folder; nothing to do"; exit 0;
    # execute_move NOT called.
```

- [ ] **Step 2:** Run, verify ImportError / failures.

- [ ] **Step 3: Implement** `find_message_anywhere` in `src/m365ctl/mail/messages.py` (alongside `find_by_internet_message_id`):

```python
def find_message_anywhere(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    internet_message_id: str,
) -> tuple[str, str] | None:
    """Locate a message by ``internetMessageId`` across the entire mailbox.

    Returns ``(message_id, parent_folder_id)`` for the first hit, or None
    when no message has that internetMessageId. Used by the undo executor
    when the message has been manually moved out of Deleted Items between
    the soft-delete and the undo (e.g. the user dragged it to Archive in
    Outlook).
    """
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    path = f"{ub}/messages"
    esc = internet_message_id.replace("'", "''")
    params = {
        "$filter": f"internetMessageId eq '{esc}'",
        "$top": 1,
        "$select": "id,parentFolderId",
    }
    raw = graph.get(path, params=params)
    items = raw.get("value", []) if isinstance(raw, dict) else []
    if not items:
        return None
    first = items[0]
    if not isinstance(first, dict):
        return None
    msg_id = first.get("id")
    parent = first.get("parentFolderId")
    if not msg_id or not parent:
        return None
    return (msg_id, parent)
```

- [ ] **Step 4: Update** `src/m365ctl/mail/cli/undo.py` — extend the mail.move branch to use the broader search.

Find the existing block (around line 166):
```python
                resolved_id = find_by_internet_message_id(
                    graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
                    folder_id="deleteditems",
                    internet_message_id=recorded_imid,
                )
                if not resolved_id:
                    print(
                        f"undo: message not found in Deleted Items "
                        f"(internetMessageId={recorded_imid!r}); the message "
                        f"may already be hard-deleted or moved manually. "
                        f"Restore manually if needed. (original error: {exc})",
                        file=sys.stderr,
                    )
                    return 1
```

Replace the `if not resolved_id:` block with the broader search:

```python
                if not resolved_id:
                    # Phase 4.x: not in Deleted Items — try the whole mailbox.
                    # Catches the "user dragged it to Archive in Outlook"
                    # case between soft-delete and undo.
                    found = find_message_anywhere(
                        graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
                        internet_message_id=recorded_imid,
                    )
                    if not found:
                        print(
                            f"undo: message not found anywhere "
                            f"(internetMessageId={recorded_imid!r}); the message "
                            f"may already be hard-deleted. "
                            f"(original error: {exc})",
                            file=sys.stderr,
                        )
                        return 1
                    resolved_id, current_folder_id = found
                    target_folder_id = rev.args.get("to_folder_id") or rev.args.get("folder_id")
                    if target_folder_id and current_folder_id == target_folder_id:
                        print(
                            f"undo: message already in original folder "
                            f"(folder_id={current_folder_id!r}); nothing to do.",
                            file=sys.stderr,
                        )
                        return 0
                    print(
                        f"undo: message manually moved to folder_id="
                        f"{current_folder_id!r}; restoring from there.",
                        file=sys.stderr,
                    )
                rev = replace(rev, item_id=resolved_id)
```

(The existing `try: msg = get_message(...)` block after this still runs to populate `current_before` for the audit record. Leave that intact.)

Imports: add `find_message_anywhere` to the existing `from m365ctl.mail.messages import ...` line.

- [ ] **Step 5:** Quality gates: pytest (947 + ~6 = ~953), mypy 0, ruff clean.

- [ ] **Step 6: Commit:**
```
git add src/m365ctl/mail/messages.py src/m365ctl/mail/cli/undo.py \
        tests/test_mail_messages.py tests/test_mail_cli_undo_recovery.py
git commit -m "fix(mail/cli/undo): broader find-anywhere fallback + already-restored short-circuit"
```

---

## Group 2 — Release 1.11.0

### Task 2.1: Bump + changelog + README + lockfile (2 commits)

- [ ] `pyproject.toml`: 1.10.0 → 1.11.0.

- [ ] Prepend CHANGELOG.md:

```markdown
## 1.11.0 — Phase 4.x: soft-delete-undo fallback cleanup

### Added
- `m365ctl.mail.messages.find_message_anywhere` — searches the whole
  mailbox by `internetMessageId` via
  `/{ub}/messages?$filter=internetMessageId eq '...'`. Returns
  `(message_id, parent_folder_id)` for the first hit, or None.

### Fixed
- `m365ctl undo <op-id>` for `mail.delete.soft` now handles two cases
  the v1.0-era recovery path missed:
  1. **Manually moved out of Deleted Items**: if the message has been
     dragged to (e.g.) Archive between the soft-delete and the undo, the
     undo finds it via `find_message_anywhere` and restores it from
     wherever it is. Stderr names the discovered folder for clarity.
  2. **Already in target folder**: if the user manually dragged it back
     to the original folder, the undo short-circuits with a "nothing to
     do" stderr notice and exits 0 (no duplicate move).

### Behaviour change
The undo error message shifts from "may already be hard-deleted or moved
manually" to "may already be hard-deleted" — the manual-move case is now
handled silently except for an informational stderr line.
```

- [ ] README Mail bullet:
```markdown
- **Soft-delete-undo cleanup (Phase 4.x, 1.11):** `m365ctl undo` for
  `mail.delete.soft` now handles manually-moved-out-of-Deleted-Items
  and already-restored cases without falling back to "restore manually".
```

- [ ] `uv sync --all-extras`. Quality gates. Two release commits per the no-amend rule.

### Task 2.2: Push, PR, merge, tag v1.11.0

Standard cadence.

---

## Self-review

**Edge case coverage:**
- ✅ Recorded id 404s but DeletedItems lookup hits → existing path (untouched).
- ✅ Recorded id 404s, DeletedItems miss, found in another folder → new fallback.
- ✅ Recorded id 404s, DeletedItems miss, found in target folder → short-circuit no-op.
- ✅ Both lookups + broader search miss → "may already be hard-deleted" error (existing exit 1 path).

**Backwards compat:**
- `find_by_internet_message_id` (folder-scoped) unchanged. The new helper sits beside it.
- The undo CLI's exit codes unchanged for unknown-message paths; the changed path is the formerly-1-now-0 (already-in-target-folder) case which is strictly improved behaviour.

**Type consistency:** `find_message_anywhere` returns `tuple[str, str] | None`, mirroring the existing helper's `str | None` return shape.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-26-phase-4-x-undo-fallbacks.md`. Branch `phase-4-x-undo-fallbacks` already off `main`.
