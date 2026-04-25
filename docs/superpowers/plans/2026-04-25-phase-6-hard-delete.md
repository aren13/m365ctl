# Phase 6 — Hard Delete + `mail clean` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan group-by-group. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Irreversible deletions with heavy guardrails. Hard-delete a single message, empty the recycle bin, or empty a named folder. Each captures the message's full EML to `logs/purged/<YYYY-MM-DD>/<op_id>.eml` BEFORE the Graph DELETE so a last-resort recovery is possible outside Graph.

**Architecture:**
- `m365ctl.mail.mutate.clean` — three executors: `execute_hard_delete`, `execute_empty_folder`, `execute_empty_recycle_bin`. Each follows the same pattern:
  1. Pre-fetch the EML(s) via `m365ctl.mail.export.eml.fetch_eml_bytes` (Phase 11 already shipped this).
  2. Write each EML to `<purged_dir>/<YYYY-MM-DD>/<op_id>.eml` (or `<op_id>/<message_id>.eml` for bulk ops).
  3. Issue the Graph DELETE.
  4. Audit log records `internet_message_id`, `subject`, `sender_address`, plus the path to the captured EML.
  5. **No undo registration** — instead, `register_irreversible(action, reason)` so `m365ctl undo <op-id>` returns a clean error pointing at the EML path.
- `m365ctl.mail.cli.clean` — single dispatcher entry handling three subcommands:
  - `mail clean <message-id>` — hard-delete one message. Always TTY-confirm, even with `--confirm`.
  - `mail clean recycle-bin` — empty Deleted Items. Always TTY-confirm.
  - `mail empty <folder>` — empty a named folder. TTY-confirm. Warns loudly on common folder names (Inbox, Sent Items, Drafts, Archive). `≥1000 items → second TTY-confirm with item count`.
- `bin/mail-clean`, `bin/mail-empty` wrappers.
- `--help` for all three opens with `"This is NOT \`mail-delete\` — these operations are IRREVERSIBLE."`.

**Tech stack:** Existing `mail.export.eml.fetch_eml_bytes`, `GraphClient.delete`, `common.audit`, `common.undo` `register_irreversible`. No new deps.

**Baseline:** `main` post-PR-#17 (6397ae1), 799 passing tests, 0 mypy errors. Tag `v1.0.0` shipped.

**Version bump:** 1.0.0 → 1.1.0.

---

## File Structure

**New:**
- `src/m365ctl/mail/mutate/clean.py` — `HardDeleteResult`, `execute_hard_delete`, `execute_empty_folder`, `execute_empty_recycle_bin`, `_capture_eml_to_purged_dir` helper.
- `src/m365ctl/mail/cli/clean.py` — argparse for `mail clean {<message-id>|recycle-bin}`.
- `src/m365ctl/mail/cli/empty.py` — argparse for `mail empty <folder>`.
- `bin/mail-clean`, `bin/mail-empty` wrappers.
- `tests/test_mail_mutate_clean.py`
- `tests/test_cli_mail_clean.py`
- `tests/test_cli_mail_empty.py`

**Modify:**
- `src/m365ctl/mail/cli/__main__.py` — route `clean` and `empty` verbs.
- `src/m365ctl/mail/mutate/undo.py` — `register_irreversible` for `mail.delete.hard`, `mail.empty.folder`, `mail.empty.recycle-bin`.
- `pyproject.toml` — bump 1.0.0 → 1.1.0.
- `CHANGELOG.md` — 1.1.0 section.
- `README.md` — Mail bullet.

---

## Group 1 — Mutate executors + irreversible registration

**Files:**
- Create: `src/m365ctl/mail/mutate/clean.py`
- Modify: `src/m365ctl/mail/mutate/undo.py`
- Create: `tests/test_mail_mutate_clean.py`

### Task 1.1: Executors + EML capture (one commit)

- [ ] **Step 1: Failing tests** at `tests/test_mail_mutate_clean.py` covering:
  - `execute_hard_delete` writes the EML to `<purged_dir>/<YYYY-MM-DD>/<op_id>.eml` BEFORE calling `graph.delete`.
  - The recorded audit `before` block contains `internet_message_id`, `subject`, `sender_address`, `purged_eml_path`.
  - On `graph.delete` failure, the EML capture is preserved (we don't roll it back).
  - On `fetch_eml_bytes` failure (e.g. message already gone), the function returns `status="error"` with a clear message and does NOT call `graph.delete`.
  - `execute_empty_folder` lists messages in the folder, writes each EML under `<purged_dir>/<YYYY-MM-DD>/<op_id>/<message_id>.eml`, then deletes them in sequence.
  - `execute_empty_recycle_bin` is `execute_empty_folder` targeted at the well-known `deleteditems` folder.
  - All three log audit start/end consistent with the existing audit API (`log_mutation_start` / `log_mutation_end`).

- [ ] **Step 2:** Run, verify ImportError.

- [ ] **Step 3: Implement** (`src/m365ctl/mail/mutate/clean.py`):

```python
"""Hard-delete + folder/recycle-bin empty executors.

Each operation captures the full EML(s) to ``[logging].purged_dir`` BEFORE
issuing the Graph DELETE. The captures live at:

    <purged_dir>/<YYYY-MM-DD>/<op_id>.eml          # single hard-delete
    <purged_dir>/<YYYY-MM-DD>/<op_id>/<msg>.eml    # bulk empty

These ops are NOT undoable — `register_irreversible` blocks any
`m365ctl undo` attempt with an error message pointing at the capture.
The capture is the only recovery path outside Graph.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.export.eml import fetch_eml_bytes


@dataclass
class HardDeleteResult:
    op_id: str
    status: str          # "ok" | "error"
    error: str | None = None
    after: dict[str, Any] = field(default_factory=dict)


def _today_dir(purged_dir: Path) -> Path:
    return purged_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _capture_eml(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    message_id: str,
    out_path: Path,
) -> bytes:
    """Fetch + persist EML; return the bytes for audit-log fields."""
    raw = fetch_eml_bytes(
        graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode, message_id=message_id,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw)
    return raw


def _peek_eml_summary(eml: bytes) -> dict[str, str]:
    """Cheap header scan for audit fields. Doesn't fully parse MIME."""
    headers = {"internet_message_id": "", "subject": "", "sender_address": ""}
    for line in eml.splitlines()[:200]:
        try:
            text = line.decode("utf-8", errors="replace")
        except Exception:
            continue
        if not text or text[0] in (" ", "\t"):
            continue  # continuation
        if ":" not in text:
            if not text.strip():
                break  # end of header block
            continue
        key, _, value = text.partition(":")
        k = key.strip().lower()
        v = value.strip()
        if k == "message-id":
            headers["internet_message_id"] = v
        elif k == "subject":
            headers["subject"] = v
        elif k == "from":
            headers["sender_address"] = v
    return headers


def execute_hard_delete(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    purged_dir: Path,
    before: dict | None = None,
) -> HardDeleteResult:
    args = op.args
    mailbox_spec = args["mailbox_spec"]
    auth_mode = args["auth_mode"]
    message_id = args["message_id"]
    capture_path = _today_dir(purged_dir) / f"{op.op_id}.eml"

    # Capture EML first. If we can't (404, auth, etc.), bail BEFORE deleting.
    try:
        eml = _capture_eml(
            graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
            message_id=message_id, out_path=capture_path,
        )
    except GraphError as e:
        log_mutation_start(
            logger, op_id=op.op_id, cmd="mail-delete-hard",
            args=args, drive_id=op.drive_id, item_id=op.item_id,
            before=before or {},
        )
        log_mutation_end(
            logger, op_id=op.op_id, after={}, result="error",
            error=f"EML capture failed before delete: {e}",
        )
        return HardDeleteResult(
            op_id=op.op_id, status="error",
            error=f"EML capture failed; refusing to delete: {e}",
        )

    summary = _peek_eml_summary(eml)
    audit_before = {**(before or {}), **summary,
                    "purged_eml_path": str(capture_path)}
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-delete-hard",
        args=args, drive_id=op.drive_id, item_id=op.item_id,
        before=audit_before,
    )

    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    try:
        graph.delete(f"{ub}/messages/{message_id}")
    except GraphError as e:
        log_mutation_end(
            logger, op_id=op.op_id, after={"purged_eml_path": str(capture_path)},
            result="error", error=str(e),
        )
        return HardDeleteResult(
            op_id=op.op_id, status="error", error=str(e),
            after={"purged_eml_path": str(capture_path)},
        )
    log_mutation_end(
        logger, op_id=op.op_id,
        after={"purged_eml_path": str(capture_path)}, result="ok",
    )
    return HardDeleteResult(
        op_id=op.op_id, status="ok",
        after={"purged_eml_path": str(capture_path)},
    )


def execute_empty_folder(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    purged_dir: Path,
    before: dict | None = None,
) -> HardDeleteResult:
    """Empty a named folder. Captures every message's EML before delete."""
    args = op.args
    mailbox_spec = args["mailbox_spec"]
    auth_mode = args["auth_mode"]
    folder_id = args["folder_id"]
    capture_root = _today_dir(purged_dir) / op.op_id

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-empty-folder",
        args=args, drive_id=op.drive_id, item_id=op.item_id,
        before=before or {},
    )

    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    list_path = f"{ub}/mailFolders/{folder_id}/messages"
    captured = 0
    failed: list[str] = []
    for items, _ in graph.get_paginated(
        list_path, params={"$select": "id", "$top": 100},
    ):
        for raw in items:
            mid = raw["id"]
            cap = capture_root / f"{mid}.eml"
            try:
                _capture_eml(
                    graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
                    message_id=mid, out_path=cap,
                )
            except GraphError as e:
                failed.append(f"{mid}: capture failed ({e})")
                continue
            try:
                graph.delete(f"{ub}/messages/{mid}")
                captured += 1
            except GraphError as e:
                failed.append(f"{mid}: delete failed ({e})")

    after = {
        "purged_count": captured,
        "purged_root": str(capture_root),
        "failures": failed,
    }
    if failed:
        log_mutation_end(
            logger, op_id=op.op_id, after=after, result="error",
            error=f"{len(failed)} per-message failures",
        )
        return HardDeleteResult(
            op_id=op.op_id, status="error",
            error="; ".join(failed[:5]) + ("…" if len(failed) > 5 else ""),
            after=after,
        )
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return HardDeleteResult(op_id=op.op_id, status="ok", after=after)


def execute_empty_recycle_bin(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    purged_dir: Path,
    before: dict | None = None,
) -> HardDeleteResult:
    """Empty Deleted Items. Wraps execute_empty_folder targeting `deleteditems`."""
    # Force the well-known folder id.
    new_args = {**op.args, "folder_id": "deleteditems"}
    op2 = Operation(
        op_id=op.op_id, action="mail.empty.recycle-bin",
        drive_id=op.drive_id, item_id=op.item_id,
        args=new_args, dry_run_result=op.dry_run_result,
    )
    return execute_empty_folder(
        op2, graph, logger, purged_dir=purged_dir, before=before,
    )
```

- [ ] **Step 4: Register irreversibles** — append to `src/m365ctl/mail/mutate/undo.py`'s `register_mail_inverses(dispatcher)` (or wherever the other Mail registrations live):

```python
dispatcher.register_irreversible(
    "mail.delete.hard",
    "Hard-delete is irreversible; recovery available only from the EML capture at logs/purged/<YYYY-MM-DD>/<op-id>.eml.",
)
dispatcher.register_irreversible(
    "mail.empty.folder",
    "Empty-folder is irreversible; per-message EMLs captured under logs/purged/<YYYY-MM-DD>/<op-id>/.",
)
dispatcher.register_irreversible(
    "mail.empty.recycle-bin",
    "Recycle-bin empty is irreversible; per-message EMLs captured under logs/purged/<YYYY-MM-DD>/<op-id>/.",
)
```

Also add `mail-delete-hard`, `mail-empty-folder`, `mail-empty-recycle-bin` branches to `build_reverse_mail_operation` that raise `Irreversible` (mirror the existing `Irreversible` pattern OneDrive uses).

- [ ] **Step 5:** Run tests + quality gates. Commit:
```
git add src/m365ctl/mail/mutate/clean.py src/m365ctl/mail/mutate/undo.py \
        tests/test_mail_mutate_clean.py
git commit -m "feat(mail/mutate): hard-delete + empty-folder/recycle-bin executors with EML capture"
```

---

## Group 2 — CLI: `mail clean` + `mail empty`

**Files:**
- Create: `src/m365ctl/mail/cli/clean.py`
- Create: `src/m365ctl/mail/cli/empty.py`
- Create: `bin/mail-clean`, `bin/mail-empty`
- Modify: `src/m365ctl/mail/cli/__main__.py`
- Create: `tests/test_cli_mail_clean.py`, `tests/test_cli_mail_empty.py`

### Task 2.1: `mail clean` (one commit)

**CLI surface:**
- `mail clean <message-id>` — hard-delete one message.
- `mail clean recycle-bin` — empty Deleted Items.

Both require BOTH `--confirm` AND a TTY confirmation. Without `--confirm`, exit 2 with stderr. With `--confirm` but no TTY available, exit 1 with `"requires TTY confirm; this is irreversible"`. With `--confirm` and TTY available, prompt `"Type 'YES' to permanently delete X (this is irreversible): "` and proceed only on exact match.

`--help` opens with: `"This is NOT \`mail-delete\` — these operations are IRREVERSIBLE."`.

- [ ] Tests at `tests/test_cli_mail_clean.py`:
  - Without `--confirm` returns 2.
  - With `--confirm` but no TTY → returns 1 with stderr.
  - With `--confirm` + TTY-stub returning "YES" → calls `execute_hard_delete`.
  - With `--confirm` + TTY-stub returning anything else → exits without calling.
  - `recycle-bin` subcommand routes to `execute_empty_recycle_bin`.

- [ ] Implement `src/m365ctl/mail/cli/clean.py`:
  - argparse with subparsers: `<message-id>` (positional fast path) OR `recycle-bin` keyword.
  - Reuse `m365ctl.common.prompts.confirm_or_abort` if it exists, else inline a `_dev_tty_yes()` helper that opens `/dev/tty` and prompts.
  - Build the `Operation`, call the matching executor with `purged_dir=cfg.logging.purged_dir`.

- [ ] Wire dispatcher: `clean` verb route in `__main__.py` + `_USAGE` line under a new "Irreversible" block:
  ```
  Irreversible (NOT undoable):
    clean        clean <message-id> | clean recycle-bin (hard delete + EML capture)
    empty        empty <folder> (hard-delete every message in the folder)
  ```

- [ ] Bin wrapper `bin/mail-clean` + `chmod +x`.

- [ ] Tests pass, mypy + ruff clean. Commit:
```
git add src/m365ctl/mail/cli/clean.py src/m365ctl/mail/cli/__main__.py \
        bin/mail-clean tests/test_cli_mail_clean.py
git commit -m "feat(mail/cli): mail clean {<message-id>|recycle-bin} with TTY confirm"
```

### Task 2.2: `mail empty <folder>` (one commit)

**CLI surface:**
- `mail empty <folder-path>` — hard-delete every message in the folder.

Guards:
1. Folder must resolve via `resolve_folder_path`.
2. Pre-flight: GET folder metadata for `totalItemCount`. If 0, exit 0 with stderr "(folder is empty)".
3. Warn if folder is one of: `Inbox`, `Sent Items`, `Drafts`, `Archive`, `Outbox` — print a yellow-flag stderr line and require an extra `--unsafe-common-folder` flag to proceed.
4. If `totalItemCount >= 1000`, print `"This will permanently delete N messages. Type 'YES DELETE N' to confirm: "` and require exact match.
5. Otherwise (1-999 items), prompt `"Type 'YES' to permanently delete N messages: "`.
6. `--help` opens with the IRREVERSIBLE warning.

- [ ] Tests covering each guard (common-folder guard, ≥1000 prompt, normal prompt, empty folder fast-exit, missing `--confirm`).

- [ ] Implement `src/m365ctl/mail/cli/empty.py`. Wire dispatcher route + bin wrapper.

- [ ] Commit:
```
feat(mail/cli): mail empty <folder> with common-folder guard + ≥1000 confirm
```

---

## Group 3 — Release 1.1.0

### Task 3.1: Bump + changelog + README + lockfile (2 commits)

- [ ] `pyproject.toml`: 1.0.0 → 1.1.0.

- [ ] Prepend CHANGELOG.md:

```markdown
## 1.1.0 — Phase 6: hard delete + `mail clean` / `mail empty`

### Added
- `m365ctl.mail.mutate.clean.execute_hard_delete` — single-message hard
  delete with EML capture to `[logging].purged_dir/<YYYY-MM-DD>/<op_id>.eml`
  BEFORE the Graph DELETE.
- `m365ctl.mail.mutate.clean.execute_empty_folder` and
  `execute_empty_recycle_bin` — bulk-delete with per-message EML capture
  to `<purged_dir>/<YYYY-MM-DD>/<op_id>/<message_id>.eml`.
- CLI: `mail clean <message-id>`, `mail clean recycle-bin`,
  `mail empty <folder-path>` — all require `--confirm` AND a TTY-typed
  confirmation phrase. Bin wrappers `bin/mail-clean`, `bin/mail-empty`.

### Safety
- `mail empty` warns on common folder names (Inbox, Sent Items, Drafts,
  Archive, Outbox) and requires `--unsafe-common-folder` to proceed.
- `mail empty` against ≥1000 items requires the operator to type
  `"YES DELETE N"` (with the exact count) before the wire-delete starts.
- All three actions are registered as **irreversible** in the undo
  dispatcher; `m365ctl undo <op-id>` returns a clear error pointing at
  the EML capture path.

### Recovery
The captured EMLs are the only recovery path outside Graph. Rotation is
governed by `[logging].retention_days` (default 30, matching Graph's
recycle-bin retention).
```

- [ ] README Mail bullet:
```markdown
- **Hard delete (Phase 6, 1.1):** `mail clean <id>`, `mail clean recycle-bin`,
  `mail empty <folder>` — irreversible deletes with full EML capture to
  `[logging].purged_dir` BEFORE the wire-delete. Triple-gated: `--confirm`,
  TTY-typed phrase, and a common-folder/≥1000-item escalation.
```

- [ ] `uv sync --all-extras`. Quality gates. Two release commits per the no-amend rule.

### Task 3.2: Push, PR, merge, tag

Push branch, open PR titled `Phase 6: hard delete + mail clean → 1.1.0`, watch CI, squash-merge, sync main, tag `v1.1.0`.

---

## Self-review

**Spec coverage (§19 Phase 6):**
- ✅ `execute_hard_delete` with EML dump BEFORE delete — G1.
- ✅ `execute_empty_folder` and `execute_empty_recycle_bin` — G1. ≥1000 → TTY confirmation in CLI — G2.
- ✅ CLI `mail clean <id>`, `mail clean recycle-bin`, `mail empty <folder>` — G2.
- ✅ Irreversible dispatcher registration with EML path in error message — G1.
- ✅ `mail clean` always requires TTY confirm even with `--confirm` — G2.
- ✅ `mail empty` warns on common folder names — G2.
- ✅ `--help` opens with "This is NOT `mail-delete`." — G2.
- ⚠️ Spec acceptance "live smoke on disposable test folder" — flagged for the next live-smoke pass, not gated in CI.
- ⚠️ Spec said bump to 0.8.0 sequentially; we bump to 1.1.0 because we shipped 8/9/10/11/14 first.

**Type consistency:** `HardDeleteResult` mirrors the existing Result-shape pattern. Audit API matches G1 of Phase 9 and Phase 8. `register_irreversible` reuses the OneDrive-side pattern.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-25-phase-6-hard-delete.md`. Branch `phase-6-hard-delete` already off `main`.
