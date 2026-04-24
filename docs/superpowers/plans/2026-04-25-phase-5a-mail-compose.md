# Phase 5a — Mail Compose: drafts, send, reply, forward, attachments (0.6.0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the compose surface — drafts (create/update/delete), send (existing draft or inline), reply (draft or inline), forward, and attachment write-side (add small, add large via upload session, remove). Drafts are fully undoable; send/reply/forward are irreversible (Dispatcher registers them with operator guidance). Bumps to 0.6.0.

**Architecture:**
- New `src/m365ctl/mail/compose.py` with shared helpers: `build_message_payload(subject, body, body_file, body_type, to, cc, bcc, importance) -> dict` + `parse_recipients(addrs: list[str]) -> list[dict]` + `count_external_recipients(recipients: list[dict], internal_domain: str | None) -> int`. Pure functions, thoroughly unit tested.
- Five new executor modules under `src/m365ctl/mail/mutate/`: `draft.py` (create/update/delete), `send.py` (send_draft/send_new + external-recipient TTY confirm), `reply.py` (create_reply/create_reply_all/send_reply_inline), `forward.py` (create_forward/send_forward_inline), `attach.py` (add_small/add_large_upload_session/remove).
- Undo: draft ops are fully reversible; send/reply/forward register as `IrreversibleOp` per spec §12.1 with actionable error messages; attach.add ↔ attach.remove. `build_reverse_mail_operation` gains branches for each reversible verb; `register_mail_inverses` gains 6 `register_irreversible(...)` calls for the unreversible ones.
- 5 new CLIs: `mail-draft` (subcommands create/update/delete), `mail-send`, `mail-reply` (default: create draft-reply; `--inline --body "..."` for one-shot send), `mail-forward`, `mail-attach` (subcommands add/remove). Each follows the Phase 3/4 single-item + `--from-plan` shape where sensible.
- `--body-file` preferred; `--body` inline with multiline warning when detected (tab/newline in input).
- `mail-send --new` honors `[mail].drafts_before_send` config — when true (default), `--new` is BLOCKED with a clear error pointing to `mail-draft create` + `mail-send <draft-id>`. Forces review-before-send ergonomics.
- `mail.send` and `mail.reply`/`mail.reply.all`/`mail.forward` record `internet_message_id` + `sent_at` in `after` per spec §13.2 idempotency. Plan re-runs skip ops where `end.after.internet_message_id` is set.
- Attachments: small (<3MB) → one-shot POST; large (≥3MB) → upload session (PUT in chunks of 4MB until last chunk). `add_attachment_large` captures `content_hash` (sha256 of raw bytes) + `name` + `size` for later idempotency checks (executor skips if an attachment with matching hash+name+size already exists).

**Tech Stack:** Python 3.11+ stdlib, httpx, msal (unchanged). `hashlib` for attachment dedupe hash. Tests use `pytest` + `MagicMock`. No new dependencies.

**Parent spec:** `docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md` — §9.2 (mutation endpoints), §10.7 (CLI shape), §11.3 (TTY confirm on >20 external recipients), §11.4 (action namespace), §12.1 (capture + inverses), §13.2 (idempotency — `internet_message_id` in after), §19 Phase 5a (deliverables + acceptance).

**Safety posture:**
- `--confirm` required for every mutation; dry-run default.
- `mail-send --new` blocked when `[mail].drafts_before_send=true` (default from Phase 0's config.toml.example) — forces draft-first workflow.
- `mail-send` with >20 external recipients → interactive `/dev/tty` confirm (non-bypassable).
- External recipient count uses `internal_domain` optionally set in config (falls back to conservative: any address outside the user's own UPN domain is external).
- All send verbs are irreversible — clearly surfaced in dry-run and in Dispatcher rejection messages.
- `assert_mail_target_allowed` runs before credential construction on all CLIs.
- Work on feature branch `phase-5a-mail-compose` off `main`.

---

## File Structure (Phase 5a target)

```
m365ctl/
├── pyproject.toml                                   # MODIFIED — version 0.6.0
├── CHANGELOG.md                                     # MODIFIED — [0.6.0] entry
├── bin/
│   ├── mail-draft                                   # NEW
│   ├── mail-send                                    # NEW
│   ├── mail-reply                                   # NEW
│   ├── mail-forward                                 # NEW
│   └── mail-attach                                   # NEW
├── src/m365ctl/
│   ├── common/planfile.py                           # MODIFIED — +9 Phase 5a actions
│   └── mail/
│       ├── compose.py                               # NEW — pure helpers (payload builder, recipients, counts)
│       ├── cli/
│       │   ├── __main__.py                          # MODIFIED — route 5 new verbs + _USAGE entries
│       │   ├── draft.py                             # NEW — create/update/delete subcommands
│       │   ├── send.py                              # NEW
│       │   ├── reply.py                             # NEW
│       │   ├── forward.py                           # NEW
│       │   ├── attach.py                            # MODIFIED (Phase 1 added list/get; add write subcommands)
│       │   └── undo.py                              # MODIFIED — route new actions to executors
│       └── mutate/
│           ├── draft.py                             # NEW
│           ├── send.py                              # NEW
│           ├── reply.py                             # NEW
│           ├── forward.py                           # NEW
│           ├── attach.py                            # NEW — write side (list/get read-side stays in mail/attachments.py)
│           └── undo.py                              # MODIFIED — reverse builders + register_mail_inverses extensions
└── tests/
    ├── test_mail_compose.py                         # NEW — pure helpers
    ├── test_mail_mutate_draft.py                    # NEW
    ├── test_mail_mutate_send.py                     # NEW
    ├── test_mail_mutate_reply.py                    # NEW
    ├── test_mail_mutate_forward.py                  # NEW
    ├── test_mail_mutate_attach.py                   # NEW
    ├── test_mail_mutate_undo_phase5a.py             # NEW
    ├── test_cli_mail_draft.py                       # NEW
    ├── test_cli_mail_send.py                        # NEW
    ├── test_cli_mail_reply.py                       # NEW
    ├── test_cli_mail_forward.py                     # NEW
    └── test_cli_mail_attach_write.py                # NEW (existing test_cli_mail_attach.py covers reader subcommands)
```

---

## Preflight

### Task 0: Branch + baseline

- [ ] **Step 1:** `git status` → clean. `git branch --show-current` → `main`.
- [ ] **Step 2:** `git checkout -b phase-5a-mail-compose`
- [ ] **Step 3:** `uv run pytest -m "not live" -q 2>&1 | tail -3` → expect **479 passed, 1 deselected**.

---

## Group 1: Planfile extension

### Task 1: Add 9 Phase 5a actions to `_VALID_ACTIONS` + `Action` Literal

**Files:**
- Modify: `src/m365ctl/common/planfile.py`
- Modify: `tests/test_planfile.py`

- [ ] **Step 1: Failing test.**

Append to `tests/test_planfile.py`:
```python
def test_plan_loader_accepts_phase5a_mail_actions(tmp_path):
    from m365ctl.common.planfile import PLAN_SCHEMA_VERSION, load_plan
    import json
    path = tmp_path / "p.json"
    path.write_text(json.dumps({
        "version": PLAN_SCHEMA_VERSION,
        "created_at": "2026-04-25T00:00:00Z",
        "source_cmd": "mail-send --from-plan",
        "scope": "me",
        "operations": [
            {"op_id": "1", "action": "mail.draft.create", "drive_id": "me", "item_id": "", "args": {"subject": "hi"}},
            {"op_id": "2", "action": "mail.draft.update", "drive_id": "me", "item_id": "d1", "args": {}},
            {"op_id": "3", "action": "mail.draft.delete", "drive_id": "me", "item_id": "d1", "args": {}},
            {"op_id": "4", "action": "mail.send",          "drive_id": "me", "item_id": "d1", "args": {}},
            {"op_id": "5", "action": "mail.reply",         "drive_id": "me", "item_id": "m1", "args": {}},
            {"op_id": "6", "action": "mail.reply.all",     "drive_id": "me", "item_id": "m1", "args": {}},
            {"op_id": "7", "action": "mail.forward",       "drive_id": "me", "item_id": "m1", "args": {}},
            {"op_id": "8", "action": "mail.attach.add",    "drive_id": "me", "item_id": "m1", "args": {}},
            {"op_id": "9", "action": "mail.attach.remove", "drive_id": "me", "item_id": "m1", "args": {}},
        ],
    }))
    plan = load_plan(path)
    assert [op.action for op in plan.operations] == [
        "mail.draft.create", "mail.draft.update", "mail.draft.delete",
        "mail.send", "mail.reply", "mail.reply.all", "mail.forward",
        "mail.attach.add", "mail.attach.remove",
    ]
```

Run: `uv run pytest tests/test_planfile.py::test_plan_loader_accepts_phase5a_mail_actions -q` → FAIL.

- [ ] **Step 2: Extend `Action` Literal + `_VALID_ACTIONS` frozenset.**

Add these 9 strings to BOTH, under a `# Phase 5a — compose.` comment:
```
"mail.draft.create", "mail.draft.update", "mail.draft.delete",
"mail.send", "mail.reply", "mail.reply.all", "mail.forward",
"mail.attach.add", "mail.attach.remove",
```

- [ ] **Step 3: Run + commit.**
```bash
uv run pytest tests/test_planfile.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/common/planfile.py tests/test_planfile.py
git commit -m "feat(planfile): accept Phase 5a compose action namespaces (draft.*/send/reply/reply.all/forward/attach.*)"
```

Expected: 479 + 1 = 480.

---

## Group 2: Compose helpers

### Task 2: `mail/compose.py` — pure helpers

**Files:**
- Create: `src/m365ctl/mail/compose.py`
- Create: `tests/test_mail_compose.py`

- [ ] **Step 1: Failing tests.**

Write `tests/test_mail_compose.py`:
```python
"""Tests for m365ctl.mail.compose — pure payload/recipient helpers."""
from __future__ import annotations

import pytest

from m365ctl.mail.compose import (
    BodyFormatError,
    build_message_payload,
    count_external_recipients,
    parse_recipients,
)


def test_parse_recipients_plain_addresses():
    assert parse_recipients(["alice@example.com", "bob@example.com"]) == [
        {"emailAddress": {"address": "alice@example.com"}},
        {"emailAddress": {"address": "bob@example.com"}},
    ]


def test_parse_recipients_name_plus_angle():
    assert parse_recipients(["Alice <alice@example.com>"]) == [
        {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
    ]


def test_parse_recipients_strips_whitespace():
    assert parse_recipients(["  alice@example.com  "]) == [
        {"emailAddress": {"address": "alice@example.com"}},
    ]


def test_parse_recipients_empty_returns_empty():
    assert parse_recipients([]) == []


def test_parse_recipients_rejects_non_email():
    with pytest.raises(ValueError):
        parse_recipients(["not-an-email"])


# ---- build_message_payload -------------------------------------------------

def test_build_message_payload_minimal_text():
    payload = build_message_payload(
        subject="Hello",
        body="Hi there",
        body_type="text",
        to=["alice@example.com"],
    )
    assert payload == {
        "subject": "Hello",
        "body": {"contentType": "text", "content": "Hi there"},
        "toRecipients": [{"emailAddress": {"address": "alice@example.com"}}],
    }


def test_build_message_payload_full_cc_bcc_importance_html():
    payload = build_message_payload(
        subject="Project update",
        body="<p>Status report</p>",
        body_type="html",
        to=["alice@example.com"],
        cc=["bob@example.com"],
        bcc=["auditor@example.com"],
        importance="high",
    )
    assert payload["body"]["contentType"] == "html"
    assert payload["body"]["content"] == "<p>Status report</p>"
    assert payload["ccRecipients"] == [{"emailAddress": {"address": "bob@example.com"}}]
    assert payload["bccRecipients"] == [{"emailAddress": {"address": "auditor@example.com"}}]
    assert payload["importance"] == "high"


def test_build_message_payload_default_body_type_is_text():
    payload = build_message_payload(
        subject="x", body="y", to=["a@example.com"],
    )
    assert payload["body"]["contentType"] == "text"


def test_build_message_payload_rejects_empty_subject_when_required():
    with pytest.raises(BodyFormatError):
        build_message_payload(
            subject="",
            body="body",
            to=["a@example.com"],
            require_subject=True,
        )


def test_build_message_payload_empty_subject_allowed_by_default():
    """Drafts may have empty subjects; only send_new requires it."""
    payload = build_message_payload(
        subject="",
        body="body",
        to=["a@example.com"],
    )
    assert payload["subject"] == ""


# ---- count_external_recipients ---------------------------------------------

def test_count_external_recipients_no_internal_domain():
    """When no internal_domain is provided, everyone counts as external."""
    recips = parse_recipients(["alice@example.com", "bob@example.com"])
    assert count_external_recipients(recips, internal_domain=None) == 2


def test_count_external_recipients_with_internal_domain():
    recips = parse_recipients([
        "alice@example.com",
        "colleague@contoso.com",
        "contractor@example.com",
    ])
    assert count_external_recipients(recips, internal_domain="contoso.com") == 2


def test_count_external_recipients_case_insensitive_domain():
    recips = parse_recipients(["CoLLeAgue@CONTOSO.com"])
    assert count_external_recipients(recips, internal_domain="contoso.com") == 0


def test_count_external_recipients_collapses_recipient_lists():
    """Count across to+cc+bcc helper call semantics: caller concatenates lists."""
    to = parse_recipients(["alice@example.com"])
    cc = parse_recipients(["bob@contoso.com"])
    bcc = parse_recipients(["carol@external.com"])
    combined = to + cc + bcc
    assert count_external_recipients(combined, internal_domain="contoso.com") == 2
```

Run: `uv run pytest tests/test_mail_compose.py -q` → all FAIL.

- [ ] **Step 2: Implement `src/m365ctl/mail/compose.py`.**

```python
"""Pure helpers for mail compose flows — no Graph calls, no side effects.

Used by the mail compose executors (``mutate/draft.py``, ``mutate/send.py``,
``mutate/reply.py``, ``mutate/forward.py``) and the CLI layer. All functions
return plain dicts / lists suitable for direct feed into Graph request JSON.
"""
from __future__ import annotations

import re
from typing import Any


class BodyFormatError(ValueError):
    """Raised when compose payload inputs are malformed."""


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NAME_EMAIL_RE = re.compile(r"^\s*(?P<name>.*?)\s*<(?P<addr>[^>]+)>\s*$")


def parse_recipients(addrs: list[str]) -> list[dict[str, Any]]:
    """Turn a list of ``["alice@ex.com", "Bob <bob@ex.com>"]`` into Graph shape.

    Returns ``[{"emailAddress": {"address": ..., "name"?: ...}}, ...]``.
    Whitespace stripped. Raises ``ValueError`` on anything that isn't a
    recognizable email address.
    """
    out: list[dict[str, Any]] = []
    for raw in addrs:
        if not raw or not raw.strip():
            continue
        s = raw.strip()
        m = _NAME_EMAIL_RE.match(s)
        if m:
            name = m.group("name")
            addr = m.group("addr").strip()
            if not _EMAIL_RE.match(addr):
                raise ValueError(f"invalid email address: {addr!r}")
            entry: dict[str, Any] = {"emailAddress": {"address": addr}}
            if name:
                entry["emailAddress"]["name"] = name
            out.append(entry)
            continue
        if _EMAIL_RE.match(s):
            out.append({"emailAddress": {"address": s}})
            continue
        raise ValueError(f"cannot parse recipient {raw!r}; expected 'addr' or 'Name <addr>'")
    return out


def build_message_payload(
    *,
    subject: str,
    body: str,
    to: list[str],
    body_type: str = "text",
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    importance: str | None = None,
    require_subject: bool = False,
) -> dict[str, Any]:
    """Assemble a Graph ``message`` JSON body.

    Only includes ``cc``/``bcc``/``importance`` keys when non-None / non-empty
    to keep request payloads compact.

    If ``require_subject`` is True and ``subject`` is empty, raise
    ``BodyFormatError`` — callers use this for ``send --new`` which refuses
    to send a blank-subject message.
    """
    if require_subject and not subject:
        raise BodyFormatError("subject cannot be empty")
    if body_type not in ("text", "html"):
        raise BodyFormatError(f"body_type must be 'text' or 'html'; got {body_type!r}")
    payload: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": body_type, "content": body},
        "toRecipients": parse_recipients(to),
    }
    if cc:
        payload["ccRecipients"] = parse_recipients(cc)
    if bcc:
        payload["bccRecipients"] = parse_recipients(bcc)
    if importance:
        payload["importance"] = importance
    return payload


def count_external_recipients(
    recipients: list[dict[str, Any]],
    *,
    internal_domain: str | None,
) -> int:
    """Return the count of recipients whose address domain is NOT ``internal_domain``.

    Case-insensitive match on domain. If ``internal_domain`` is None, all
    recipients count as external (the cautious default).
    """
    if internal_domain is None:
        return len(recipients)
    needle = "@" + internal_domain.lower()
    count = 0
    for r in recipients:
        addr = (r.get("emailAddress", {}).get("address") or "").lower()
        if not addr.endswith(needle):
            count += 1
    return count
```

- [ ] **Step 3: Run + commit.**
```bash
uv run pytest tests/test_mail_compose.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/compose.py tests/test_mail_compose.py
git commit -m "feat(mail): compose — parse_recipients + build_message_payload + count_external_recipients"
```

Expected: 480 + 15 = 495.

---

## Group 3: Draft executors

### Task 3: `mail/mutate/draft.py` — create/update/delete

**Files:**
- Create: `src/m365ctl/mail/mutate/draft.py`
- Create: `tests/test_mail_mutate_draft.py`

Graph endpoints:
- `create_draft`: POST `{ub}/messages` with a message payload → returns the new draft. Graph places it in the Drafts folder automatically.
- `update_draft`: PATCH `{ub}/messages/{id}` with fields to update.
- `delete_draft`: DELETE `{ub}/messages/{id}` — this is a HARD delete (drafts aren't soft-deleted via Drafts→Deleted Items in this API), so undo captures the full draft body and rebuilds it via create_draft.

- [ ] **Step 1: Failing tests.**

Write `tests/test_mail_mutate_draft.py`:
```python
"""Tests for m365ctl.mail.mutate.draft — create/update/delete draft executors."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.draft import (
    execute_create_draft,
    execute_delete_draft,
    execute_update_draft,
)


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_create_draft_posts_payload_and_records_new_id(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {
        "id": "draft-1", "subject": "Hello",
        "webLink": "https://outlook.office.com/?ItemID=d1",
    }
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-d", action="mail.draft.create",
        drive_id="me", item_id="",
        args={
            "subject": "Hello",
            "body": "Hi there",
            "body_type": "text",
            "to": ["alice@example.com"],
            "cc": [],
            "bcc": [],
        },
    )
    result = execute_create_draft(op, graph, logger, before={})
    assert result.status == "ok"
    assert result.after == {
        "id": "draft-1",
        "web_link": "https://outlook.office.com/?ItemID=d1",
    }
    assert graph.post.call_args.args[0] == "/me/messages"
    body = graph.post.call_args.kwargs["json"]
    assert body["subject"] == "Hello"
    assert body["body"] == {"contentType": "text", "content": "Hi there"}
    assert body["toRecipients"] == [{"emailAddress": {"address": "alice@example.com"}}]
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-draft-create"


def test_update_draft_patches_fields(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "d1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-u", action="mail.draft.update",
        drive_id="me", item_id="d1",
        args={"subject": "Updated", "body": "new body"},
    )
    prior = {
        "subject": "Original",
        "body": {"contentType": "text", "content": "old body"},
        "toRecipients": [{"emailAddress": {"address": "alice@example.com"}}],
    }
    result = execute_update_draft(op, graph, logger, before=prior)
    assert result.status == "ok"
    assert graph.patch.call_args.args[0] == "/me/messages/d1"
    patch_body = graph.patch.call_args.kwargs["json_body"]
    assert patch_body["subject"] == "Updated"
    assert patch_body["body"] == {"contentType": "text", "content": "new body"}
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-draft-update"
    assert entries[0]["before"]["subject"] == "Original"


def test_update_draft_partial_only_sends_specified_fields(tmp_path):
    """Subject-only update: body/recipients NOT touched."""
    graph = MagicMock()
    graph.patch.return_value = {"id": "d1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-partial", action="mail.draft.update",
        drive_id="me", item_id="d1",
        args={"subject": "just subject"},
    )
    execute_update_draft(op, graph, logger, before={"subject": "old"})
    patch_body = graph.patch.call_args.kwargs["json_body"]
    assert patch_body == {"subject": "just subject"}


def test_delete_draft_captures_full_content_before_delete(tmp_path):
    """Full draft body must be in `before` so undo can recreate."""
    graph = MagicMock()
    graph.delete.return_value = None
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-del", action="mail.draft.delete",
        drive_id="me", item_id="d1",
        args={},
    )
    prior = {
        "subject": "Draft subject",
        "body": {"contentType": "text", "content": "body text"},
        "toRecipients": [{"emailAddress": {"address": "alice@example.com"}}],
        "ccRecipients": [],
        "bccRecipients": [],
    }
    result = execute_delete_draft(op, graph, logger, before=prior)
    assert result.status == "ok"
    assert graph.delete.call_args.args[0] == "/me/messages/d1"
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-draft-delete"
    assert entries[0]["before"]["subject"] == "Draft subject"
    assert entries[0]["before"]["body"]["content"] == "body text"


def test_create_draft_graph_error(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.post.side_effect = GraphError("conflict")
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-err", action="mail.draft.create",
        drive_id="me", item_id="",
        args={"subject": "x", "body": "y", "to": ["a@example.com"]},
    )
    result = execute_create_draft(op, graph, logger, before={})
    assert result.status == "error"
    assert "conflict" in (result.error or "")


def test_create_draft_app_only_routes_via_users_upn(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "d2", "webLink": "x"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-ao", action="mail.draft.create",
        drive_id="bob@example.com", item_id="",
        args={
            "subject": "hi", "body": "y", "to": ["a@example.com"],
            "auth_mode": "app-only",
        },
    )
    execute_create_draft(op, graph, logger, before={})
    assert graph.post.call_args.args[0] == "/users/bob@example.com/messages"
```

Run: `uv run pytest tests/test_mail_mutate_draft.py -q` → all FAIL.

- [ ] **Step 2: Implement `src/m365ctl/mail/mutate/draft.py`.**

```python
"""Draft CRUD — create/update/delete, all undoable.

- ``execute_create_draft`` — POST /messages with a message payload. Graph
  places it in Drafts automatically. `before` is empty; `after` captures
  the new id + webLink.
- ``execute_update_draft`` — PATCH /messages/{id} with the subset of fields
  the user wants to change. `before` is the full prior draft so undo can
  restore it.
- ``execute_delete_draft`` — DELETE /messages/{id} (hard delete — drafts
  skip Deleted Items per Graph semantics). `before` captures the full
  draft body so undo can recreate it via create.
"""
from __future__ import annotations

from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.compose import build_message_payload
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.mutate._common import MailResult


def _user_base(op: Operation) -> str:
    auth_mode = op.args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)


def execute_create_draft(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /messages with the composed payload; Graph creates it in Drafts."""
    ub = _user_base(op)
    payload = build_message_payload(
        subject=op.args.get("subject", ""),
        body=op.args.get("body", ""),
        body_type=op.args.get("body_type", "text"),
        to=list(op.args.get("to", [])),
        cc=list(op.args.get("cc", []) or []),
        bcc=list(op.args.get("bcc", []) or []),
        importance=op.args.get("importance"),
    )
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-draft-create",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        created = graph.post(f"{ub}/messages", json=payload)
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {
        "id": created.get("id", ""),
        "web_link": created.get("webLink", ""),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_update_draft(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """PATCH /messages/{id} with the subset of fields specified in op.args."""
    ub = _user_base(op)
    payload: dict[str, Any] = {}
    if "subject" in op.args:
        payload["subject"] = op.args["subject"]
    if "body" in op.args:
        payload["body"] = {"contentType": op.args.get("body_type", "text"),
                           "content": op.args["body"]}
    if "to" in op.args:
        from m365ctl.mail.compose import parse_recipients
        payload["toRecipients"] = parse_recipients(list(op.args["to"]))
    if "cc" in op.args:
        from m365ctl.mail.compose import parse_recipients
        payload["ccRecipients"] = parse_recipients(list(op.args["cc"]))
    if "bcc" in op.args:
        from m365ctl.mail.compose import parse_recipients
        payload["bccRecipients"] = parse_recipients(list(op.args["bcc"]))
    if "importance" in op.args:
        payload["importance"] = op.args["importance"]

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-draft-update",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.patch(f"{ub}/messages/{op.item_id}", json_body=payload)
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    log_mutation_end(logger, op_id=op.op_id, after={"updated": True}, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after={"updated": True})


def execute_delete_draft(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """DELETE /messages/{id}. ``before`` MUST contain the full draft so undo can recreate."""
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-draft-delete",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.delete(f"{ub}/messages/{op.item_id}")
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    log_mutation_end(logger, op_id=op.op_id, after=None, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=None)
```

- [ ] **Step 3: Run + commit.**
```bash
uv run pytest tests/test_mail_mutate_draft.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/mutate/draft.py tests/test_mail_mutate_draft.py
git commit -m "feat(mail/mutate): draft executors — create/update/delete with audit capture for undo"
```

Expected: 495 + 6 = 501.

---

## Group 4: Send executors + external-recipient TTY confirm

### Task 4: `mail/mutate/send.py` — send_draft + send_new

**Files:**
- Create: `src/m365ctl/mail/mutate/send.py`
- Create: `tests/test_mail_mutate_send.py`

Graph endpoints:
- `send_draft`: POST `{ub}/messages/{id}/send` — sends an existing draft. No body; the request body is empty. Graph returns 202; we don't get the `internet_message_id` back from that call directly. To capture it, we fetch the message after send — but Graph moves the sent message to Sent Items, so `get_message` on the draft id will fail with 404 after send. **Workaround:** the server's `202 Accepted` response has no body; there is no reliable way to recover the `internet_message_id` post-send via this endpoint without additional calls. Record in `after` whatever metadata IS available — `sent_at = datetime.now(timezone.utc).isoformat()` at least, and leave `internet_message_id` empty for now. (Phase 5a documents this gap; Phase 7 catalog crawl will backfill via `/me/messages` after a `/delta` sync.)
- `send_new`: POST `{ub}/sendMail` with `{"message": <payload>, "saveToSentItems": true}`. Nothing persistent is created; no id returned. Same `sent_at` capture.

- [ ] **Step 1: Failing tests.**

Write `tests/test_mail_mutate_send.py`:
```python
"""Tests for m365ctl.mail.mutate.send — send_draft + send_new."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.send import execute_send_draft, execute_send_new


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_send_draft_posts_send_endpoint(tmp_path):
    graph = MagicMock()
    graph.post_raw.return_value = MagicMock(status_code=202, headers={})
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-s", action="mail.send",
        drive_id="me", item_id="d1",
        args={},
    )
    result = execute_send_draft(op, graph, logger, before={})
    assert result.status == "ok"
    assert "sent_at" in (result.after or {})
    assert graph.post_raw.call_args.args[0] == "/me/messages/d1/send"
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-send"


def test_send_draft_graph_error(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.post_raw.side_effect = GraphError("mailbox quota exceeded")
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-err", action="mail.send",
        drive_id="me", item_id="d1",
        args={},
    )
    result = execute_send_draft(op, graph, logger, before={})
    assert result.status == "error"
    assert "quota" in (result.error or "")


def test_send_new_posts_sendMail_with_wrapped_payload(tmp_path):
    graph = MagicMock()
    graph.post_raw.return_value = MagicMock(status_code=202, headers={})
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-n", action="mail.send",
        drive_id="me", item_id="",
        args={
            "subject": "Hello",
            "body": "Body text",
            "to": ["alice@example.com"],
            "new": True,
        },
    )
    result = execute_send_new(op, graph, logger, before={})
    assert result.status == "ok"
    assert "sent_at" in (result.after or {})
    assert graph.post_raw.call_args.args[0] == "/me/sendMail"
    payload = graph.post_raw.call_args.kwargs["json_body"]
    assert payload["saveToSentItems"] is True
    assert payload["message"]["subject"] == "Hello"
    assert payload["message"]["toRecipients"] == [{"emailAddress": {"address": "alice@example.com"}}]


def test_send_new_rejects_empty_subject(tmp_path):
    """send_new requires a subject — unlike drafts."""
    from m365ctl.mail.compose import BodyFormatError
    import pytest
    graph = MagicMock()
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-blank", action="mail.send",
        drive_id="me", item_id="",
        args={"subject": "", "body": "body", "to": ["a@example.com"], "new": True},
    )
    result = execute_send_new(op, graph, logger, before={})
    # send_new surfaces BodyFormatError as a clean "error" MailResult.
    assert result.status == "error"
    assert "subject" in (result.error or "").lower()


def test_send_new_app_only_routes_via_users_upn(tmp_path):
    graph = MagicMock()
    graph.post_raw.return_value = MagicMock(status_code=202, headers={})
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-ao", action="mail.send",
        drive_id="bob@example.com", item_id="",
        args={
            "subject": "x", "body": "y", "to": ["a@example.com"],
            "new": True, "auth_mode": "app-only",
        },
    )
    execute_send_new(op, graph, logger, before={})
    assert graph.post_raw.call_args.args[0] == "/users/bob@example.com/sendMail"
```

Run: `uv run pytest tests/test_mail_mutate_send.py -q` → all FAIL.

- [ ] **Step 2: Implement `src/m365ctl/mail/mutate/send.py`.**

Uses `graph.post_raw` (existing method that returns the raw `httpx.Response`) so we can inspect the 202 status cleanly. The `post_raw` method already exists — see `src/m365ctl/common/graph.py:162`.

```python
"""Send executors — send_draft (existing) + send_new (inline).

Both end up invoking Graph endpoints that return 202 Accepted with no
response body. We can't recover ``internet_message_id`` from the response
directly — catalog crawls (Phase 7) backfill via ``/me/messages`` delta.
For now, ``after`` captures ``sent_at`` (our local UTC timestamp) and
an empty ``internet_message_id`` placeholder. Spec §13.2 idempotency is
honored by the CLI layer: plan re-runs skip ops where ``end.after`` has
``sent_at`` set.

``mail.send`` is registered as an ``IrreversibleOp`` in the Dispatcher
(see ``mail/mutate/undo.py``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.compose import BodyFormatError, build_message_payload
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.mutate._common import MailResult


def _user_base(op: Operation) -> str:
    auth_mode = op.args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def execute_send_draft(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /messages/{id}/send (202 Accepted, no body)."""
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-send",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.post_raw(f"{ub}/messages/{op.item_id}/send")
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {
        "sent_at": _now_utc_iso(),
        "internet_message_id": "",  # backfilled by catalog crawl (Phase 7)
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_send_new(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /sendMail with {message, saveToSentItems: true}."""
    ub = _user_base(op)
    try:
        message = build_message_payload(
            subject=op.args.get("subject", ""),
            body=op.args.get("body", ""),
            body_type=op.args.get("body_type", "text"),
            to=list(op.args.get("to", [])),
            cc=list(op.args.get("cc", []) or []),
            bcc=list(op.args.get("bcc", []) or []),
            importance=op.args.get("importance"),
            require_subject=True,
        )
    except BodyFormatError as e:
        log_mutation_start(
            logger, op_id=op.op_id, cmd="mail-send",
            args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
        )
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))

    payload = {"message": message, "saveToSentItems": True}
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-send",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.post_raw(f"{ub}/sendMail", json_body=payload)
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {
        "sent_at": _now_utc_iso(),
        "internet_message_id": "",
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
```

**IMPORTANT:** `GraphClient.post_raw` currently has signature `post_raw(path, *, json_body: dict | None = None)`. Confirm by reading `src/m365ctl/common/graph.py:162`. If the signature is different, adapt.

- [ ] **Step 3: Run + commit.**
```bash
uv run pytest tests/test_mail_mutate_send.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/mutate/send.py tests/test_mail_mutate_send.py
git commit -m "feat(mail/mutate): send executors — send_draft + send_new; sent_at in after"
```

Expected: 501 + 5 = 506.

---

## Group 5: Reply + forward executors

### Task 5: `mail/mutate/reply.py` + `mail/mutate/forward.py`

Graph endpoints:
- `create_reply`: POST `{ub}/messages/{id}/createReply` → returns a new draft-reply with the quoted original.
- `create_reply_all`: POST `{ub}/messages/{id}/createReplyAll` → similar.
- `create_forward`: POST `{ub}/messages/{id}/createForward` → similar.
- `send_reply_inline`: POST `{ub}/messages/{id}/reply` with `{"comment": "body"}` → one-shot send. Same for `replyAll` / `forward` endpoints (POST with `{"comment": ..., "toRecipients": [...]}`).

Two files, one commit. Tests mirror Phase 2/4 style.

- [ ] **Step 1: Failing tests.**

Write `tests/test_mail_mutate_reply.py`:
```python
from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.reply import (
    execute_create_reply,
    execute_create_reply_all,
    execute_send_reply_inline,
)


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_create_reply_posts_createReply(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "reply-1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-r", action="mail.reply",
        drive_id="me", item_id="m1",
        args={"mode": "create"},
    )
    result = execute_create_reply(op, graph, logger, before={})
    assert result.status == "ok"
    assert result.after == {"draft_id": "reply-1"}
    assert graph.post.call_args.args[0] == "/me/messages/m1/createReply"
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-reply(create)"


def test_create_reply_all_posts_createReplyAll(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "reply-all-1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-ra", action="mail.reply.all",
        drive_id="me", item_id="m1",
        args={"mode": "create"},
    )
    result = execute_create_reply_all(op, graph, logger, before={})
    assert result.status == "ok"
    assert result.after == {"draft_id": "reply-all-1"}
    assert graph.post.call_args.args[0] == "/me/messages/m1/createReplyAll"
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-reply.all(create)"


def test_send_reply_inline_posts_reply_endpoint_with_comment(tmp_path):
    graph = MagicMock()
    graph.post_raw.return_value = MagicMock(status_code=202, headers={})
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-i", action="mail.reply",
        drive_id="me", item_id="m1",
        args={"mode": "inline", "body": "ok"},
    )
    result = execute_send_reply_inline(op, graph, logger, before={})
    assert result.status == "ok"
    assert "sent_at" in (result.after or {})
    assert graph.post_raw.call_args.args[0] == "/me/messages/m1/reply"
    body = graph.post_raw.call_args.kwargs["json_body"]
    assert body == {"comment": "ok"}
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-reply(inline)"
```

Write `tests/test_mail_mutate_forward.py`:
```python
from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.forward import execute_create_forward, execute_send_forward_inline


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


def test_create_forward_posts_createForward(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "fwd-1"}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-f", action="mail.forward",
        drive_id="me", item_id="m1",
        args={"mode": "create"},
    )
    result = execute_create_forward(op, graph, logger, before={})
    assert result.status == "ok"
    assert result.after == {"draft_id": "fwd-1"}
    assert graph.post.call_args.args[0] == "/me/messages/m1/createForward"
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-forward(create)"


def test_send_forward_inline_includes_to_recipients(tmp_path):
    graph = MagicMock()
    graph.post_raw.return_value = MagicMock(status_code=202, headers={})
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-fi", action="mail.forward",
        drive_id="me", item_id="m1",
        args={"mode": "inline", "body": "fyi", "to": ["carol@example.com"]},
    )
    result = execute_send_forward_inline(op, graph, logger, before={})
    assert result.status == "ok"
    assert graph.post_raw.call_args.args[0] == "/me/messages/m1/forward"
    body = graph.post_raw.call_args.kwargs["json_body"]
    assert body["comment"] == "fyi"
    assert body["toRecipients"] == [{"emailAddress": {"address": "carol@example.com"}}]
```

Run: `uv run pytest tests/test_mail_mutate_reply.py tests/test_mail_mutate_forward.py -q` → all FAIL.

- [ ] **Step 2: Implement `src/m365ctl/mail/mutate/reply.py`.**

```python
"""Reply executors — create_reply, create_reply_all, send_reply_inline.

- ``execute_create_reply`` — POST /messages/{id}/createReply → returns a
  new draft-reply (the caller can then update + send via mail-draft/mail-send).
- ``execute_create_reply_all`` — POST /messages/{id}/createReplyAll → same shape.
- ``execute_send_reply_inline`` — POST /messages/{id}/reply with
  ``{"comment": "body"}`` → one-shot send (no persistent draft created).

All three are wrapped with audit log start/end. ``mail.reply`` + ``mail.reply.all``
are Irreversible per spec §12.1 (outgoing mail cannot be recalled).
"""
from __future__ import annotations

from datetime import datetime, timezone
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


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def execute_create_reply(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-reply(create)",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        draft = graph.post(f"{ub}/messages/{op.item_id}/createReply", json={})
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"draft_id": draft.get("id", "")}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_create_reply_all(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-reply.all(create)",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        draft = graph.post(f"{ub}/messages/{op.item_id}/createReplyAll", json={})
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"draft_id": draft.get("id", "")}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_send_reply_inline(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /messages/{id}/reply with {comment: body}. One-shot, 202 Accepted."""
    ub = _user_base(op)
    payload = {"comment": op.args.get("body", "")}
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-reply(inline)",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.post_raw(f"{ub}/messages/{op.item_id}/reply", json_body=payload)
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"sent_at": _now_utc_iso(), "internet_message_id": ""}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
```

- [ ] **Step 3: Implement `src/m365ctl/mail/mutate/forward.py`.**

```python
"""Forward executors — create_forward + send_forward_inline."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.compose import parse_recipients
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.mutate._common import MailResult


def _user_base(op: Operation) -> str:
    auth_mode = op.args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def execute_create_forward(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    ub = _user_base(op)
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-forward(create)",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        draft = graph.post(f"{ub}/messages/{op.item_id}/createForward", json={})
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"draft_id": draft.get("id", "")}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_send_forward_inline(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    ub = _user_base(op)
    payload: dict[str, Any] = {"comment": op.args.get("body", "")}
    recipients = op.args.get("to") or []
    if recipients:
        payload["toRecipients"] = parse_recipients(list(recipients))
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-forward(inline)",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.post_raw(f"{ub}/messages/{op.item_id}/forward", json_body=payload)
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"sent_at": _now_utc_iso(), "internet_message_id": ""}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
```

- [ ] **Step 4: Run + commit.**
```bash
uv run pytest tests/test_mail_mutate_reply.py tests/test_mail_mutate_forward.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/mutate/reply.py src/m365ctl/mail/mutate/forward.py tests/test_mail_mutate_reply.py tests/test_mail_mutate_forward.py
git commit -m "feat(mail/mutate): reply + forward — create/inline executors with audit capture"
```

Expected: 506 + 3 + 2 = 511.

---

## Group 6: Attachment write executors

### Task 6: `mail/mutate/attach.py` — add_small + add_large_upload_session + remove

**Files:**
- Create: `src/m365ctl/mail/mutate/attach.py`
- Create: `tests/test_mail_mutate_attach.py`

Graph endpoints:
- `add_small` (< 3MB): POST `{ub}/messages/{id}/attachments` with `{"@odata.type": "#microsoft.graph.fileAttachment", "name": ..., "contentBytes": base64(...)}`.
- `add_large_upload_session` (≥ 3MB): POST `{ub}/messages/{id}/attachments/createUploadSession` → returns `uploadUrl`. Then PUT to that URL in chunks with `Content-Range` headers.
- `remove`: DELETE `{ub}/messages/{id}/attachments/{aid}`. Before captures full attachment bytes.

For Phase 5a scope, **add_small covers the core case**; add_large is non-trivial (chunked upload). We implement both but the chunked upload uses a simple bytestring approach (no streaming): read the whole file into memory, chunk-split at 4MB boundaries.

**Threshold: 3MB** per spec §10.7.

Also: **idempotency** per spec §13.2 — executor hashes file bytes + name + content-id. Phase 5a implements the hash capture in `after`; plan re-runs dedup logic is CLI-layer concern (deferred to Phase 7 when catalog gives us a way to query existing attachments).

- [ ] **Step 1: Failing tests.**

Write `tests/test_mail_mutate_attach.py`:
```python
"""Tests for m365ctl.mail.mutate.attach — add (small + large), remove."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, iter_audit_entries
from m365ctl.common.planfile import Operation
from m365ctl.mail.mutate.attach import (
    execute_add_attachment_small,
    execute_remove_attachment,
    pick_upload_strategy,
)


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "ops")


# ---- pick_upload_strategy --------------------------------------------------

def test_pick_upload_strategy_under_3mb_returns_small():
    assert pick_upload_strategy(size=1024) == "small"
    assert pick_upload_strategy(size=3 * 1024 * 1024 - 1) == "small"


def test_pick_upload_strategy_3mb_exact_returns_large():
    assert pick_upload_strategy(size=3 * 1024 * 1024) == "large"


def test_pick_upload_strategy_above_3mb_returns_large():
    assert pick_upload_strategy(size=10 * 1024 * 1024) == "large"


# ---- add_small -------------------------------------------------------------

def test_add_small_posts_base64_inline(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "att-new", "name": "x.pdf", "size": 5}
    logger = _logger(tmp_path)
    file_bytes = b"hello"
    op = Operation(
        op_id="op-a", action="mail.attach.add",
        drive_id="me", item_id="m1",
        args={
            "name": "x.pdf",
            "content_type": "application/pdf",
            "content_bytes_b64": base64.b64encode(file_bytes).decode("ascii"),
        },
    )
    result = execute_add_attachment_small(op, graph, logger, before={})
    assert result.status == "ok"
    assert result.after["id"] == "att-new"
    assert result.after["name"] == "x.pdf"
    assert result.after["size"] == 5
    # sha256 hash is captured in after for idempotency.
    assert result.after["content_hash"] == hashlib.sha256(file_bytes).hexdigest()

    path = graph.post.call_args.args[0]
    assert path == "/me/messages/m1/attachments"
    body = graph.post.call_args.kwargs["json"]
    assert body["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert body["name"] == "x.pdf"
    assert body["contentType"] == "application/pdf"
    assert body["contentBytes"] == base64.b64encode(file_bytes).decode("ascii")

    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-attach-add"


def test_add_small_app_only_routing(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {"id": "a2", "name": "y", "size": 1}
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-ao", action="mail.attach.add",
        drive_id="bob@example.com", item_id="m1",
        args={
            "name": "y", "content_type": "text/plain",
            "content_bytes_b64": base64.b64encode(b"X").decode("ascii"),
            "auth_mode": "app-only",
        },
    )
    execute_add_attachment_small(op, graph, logger, before={})
    assert graph.post.call_args.args[0] == "/users/bob@example.com/messages/m1/attachments"


# ---- remove ----------------------------------------------------------------

def test_remove_attachment_deletes_and_records_before_bytes(tmp_path):
    graph = MagicMock()
    graph.delete.return_value = None
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-rm", action="mail.attach.remove",
        drive_id="me", item_id="m1",
        args={"attachment_id": "att-1"},
    )
    prior = {
        "id": "att-1", "name": "report.pdf", "content_type": "application/pdf",
        "size": 1234,
        "content_bytes_b64": base64.b64encode(b"pdf-bytes").decode("ascii"),
    }
    result = execute_remove_attachment(op, graph, logger, before=prior)
    assert result.status == "ok"
    assert graph.delete.call_args.args[0] == "/me/messages/m1/attachments/att-1"
    entries = list(iter_audit_entries(logger))
    assert entries[0]["cmd"] == "mail-attach-remove"
    # The full bytes are captured for undo reconstruction.
    assert entries[0]["before"]["content_bytes_b64"]


def test_remove_attachment_graph_error(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.delete.side_effect = GraphError("not found")
    logger = _logger(tmp_path)
    op = Operation(
        op_id="op-err", action="mail.attach.remove",
        drive_id="me", item_id="m1",
        args={"attachment_id": "missing"},
    )
    result = execute_remove_attachment(op, graph, logger, before={})
    assert result.status == "error"
```

Run: `uv run pytest tests/test_mail_mutate_attach.py -q` → 7 FAIL.

- [ ] **Step 2: Implement `src/m365ctl/mail/mutate/attach.py`.**

```python
"""Attachment write executors — add (small + large via upload session) + remove.

Small threshold: 3 MB (spec §10.7). Graph's hard limit for the inline
``POST /attachments`` endpoint. Above that, use ``createUploadSession`` + PUT
in ~4 MB chunks with ``Content-Range`` headers.

Idempotency (spec §13.2): executor captures ``sha256(content_bytes) + name + size``
in ``after``. The CLI layer (Phase 7+, with catalog) can skip ops where a
matching attachment already exists on the message.

Attach remove captures the full attachment bytes in ``before`` so undo can
recreate via ``add_small`` (or ``add_large_upload_session`` for big files —
Phase 6+ extends undo to choose strategy based on size).
"""
from __future__ import annotations

import base64
import hashlib
from typing import Any, Literal

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.mutate._common import MailResult


_SMALL_THRESHOLD_BYTES = 3 * 1024 * 1024  # 3 MB
_UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024     # 4 MB


def _user_base(op: Operation) -> str:
    auth_mode = op.args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)


def pick_upload_strategy(*, size: int) -> Literal["small", "large"]:
    """Choose the upload strategy for an attachment of the given size.

    - < 3 MB: small (inline ``POST /attachments`` with base64 contentBytes).
    - >= 3 MB: large (``createUploadSession`` + chunked PUT).
    """
    return "small" if size < _SMALL_THRESHOLD_BYTES else "large"


def execute_add_attachment_small(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /messages/{id}/attachments with fileAttachment.

    ``op.args`` must contain:
    - ``name``: filename
    - ``content_type``: MIME type (e.g. ``"application/pdf"``)
    - ``content_bytes_b64``: base64-encoded file bytes
    """
    ub = _user_base(op)
    name = op.args["name"]
    content_type = op.args.get("content_type", "application/octet-stream")
    content_b64 = op.args["content_bytes_b64"]

    # Hash for idempotency: operates on raw bytes so must decode first.
    try:
        raw = base64.b64decode(content_b64)
    except Exception as e:
        log_mutation_start(
            logger, op_id=op.op_id, cmd="mail-attach-add",
            args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
        )
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error",
                         error=f"invalid base64 content: {e}")
        return MailResult(op_id=op.op_id, status="error", error=f"invalid base64 content: {e}")

    content_hash = hashlib.sha256(raw).hexdigest()
    payload: dict[str, Any] = {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": name,
        "contentType": content_type,
        "contentBytes": content_b64,
    }

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-attach-add",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        created = graph.post(f"{ub}/messages/{op.item_id}/attachments", json=payload)
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {
        "id": created.get("id", ""),
        "name": created.get("name", name),
        "size": created.get("size", len(raw)),
        "content_hash": content_hash,
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_remove_attachment(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """DELETE /messages/{id}/attachments/{aid}. ``before`` captures full attachment for undo."""
    ub = _user_base(op)
    attachment_id = op.args["attachment_id"]
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-attach-remove",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    try:
        graph.delete(f"{ub}/messages/{op.item_id}/attachments/{attachment_id}")
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    log_mutation_end(logger, op_id=op.op_id, after=None, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=None)
```

**Note on large-attachment upload:** Phase 5a ships `pick_upload_strategy` + the small/remove executors. **`execute_add_attachment_large_upload_session` is stubbed for Phase 5a-2** — a follow-up phase (5a-2 or 6) implements the chunked upload. The CLI layer detects `size >= 3MB` and prints a clear "attachment too large for Phase 5a inline flow; arrives in Phase 5a-2" error until then.

- [ ] **Step 3: Run + commit.**
```bash
uv run pytest tests/test_mail_mutate_attach.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/mutate/attach.py tests/test_mail_mutate_attach.py
git commit -m "feat(mail/mutate): attachment write — add_small + remove + pick_upload_strategy; large deferred"
```

Expected: 511 + 7 = 518.

---

## Group 7: Undo extensions

### Task 7: Extend `mail/mutate/undo.py` + `mail/cli/undo.py` for Phase 5a verbs

**Files:**
- Modify: `src/m365ctl/mail/mutate/undo.py`
- Modify: `src/m365ctl/mail/cli/undo.py`
- Create: `tests/test_mail_mutate_undo_phase5a.py`

Reversibility map (spec §12.1):
- `mail.draft.create` → inverse `mail.draft.delete` on `after.id`
- `mail.draft.update` → inverse `mail.draft.update` with `before` fields (full prior draft)
- `mail.draft.delete` → inverse `mail.draft.create` from captured `before`
- `mail.send`, `mail.reply`, `mail.reply.all`, `mail.forward` → **Irreversible** (register with operator-guidance reasons)
- `mail.attach.add` → inverse `mail.attach.remove` on `after.id`
- `mail.attach.remove` → inverse `mail.attach.add` from captured `before.content_bytes_b64`

- [ ] **Step 1: Failing tests.**

Write `tests/test_mail_mutate_undo_phase5a.py`:
```python
"""Reverse-op tests for Phase 5a compose verbs."""
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


def _record(logger, *, op_id, cmd, drive_id, item_id, args, before, after):
    log_mutation_start(logger, op_id=op_id, cmd=cmd, args=args,
                       drive_id=drive_id, item_id=item_id, before=before)
    log_mutation_end(logger, op_id=op_id, after=after, result="ok")


def test_reverse_draft_create_emits_draft_delete(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-dc", cmd="mail-draft-create",
        drive_id="me", item_id="",
        args={"subject": "Hi", "body": "x", "to": ["a@example.com"]},
        before={},
        after={"id": "new-draft", "web_link": "x"},
    )
    rev = build_reverse_mail_operation(logger, "op-dc")
    assert rev.action == "mail.draft.delete"
    assert rev.item_id == "new-draft"


def test_reverse_draft_update_restores_prior(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-du", cmd="mail-draft-update",
        drive_id="me", item_id="d1",
        args={"subject": "New"},
        before={"subject": "Old", "body": {"contentType": "text", "content": "old body"}},
        after={"updated": True},
    )
    rev = build_reverse_mail_operation(logger, "op-du")
    assert rev.action == "mail.draft.update"
    assert rev.args["subject"] == "Old"


def test_reverse_draft_delete_emits_create_from_captured(tmp_path):
    logger = _logger(tmp_path)
    prior = {
        "subject": "Lost", "body": {"contentType": "text", "content": "body"},
        "toRecipients": [{"emailAddress": {"address": "a@example.com"}}],
    }
    _record(
        logger, op_id="op-dd", cmd="mail-draft-delete",
        drive_id="me", item_id="d1",
        args={}, before=prior, after=None,
    )
    rev = build_reverse_mail_operation(logger, "op-dd")
    assert rev.action == "mail.draft.create"
    assert rev.args["subject"] == "Lost"


def test_reverse_draft_delete_rejects_empty_before(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-bad", cmd="mail-draft-delete",
        drive_id="me", item_id="d1", args={}, before={}, after=None,
    )
    with pytest.raises(Irreversible):
        build_reverse_mail_operation(logger, "op-bad")


def test_reverse_attach_add_emits_remove(tmp_path):
    logger = _logger(tmp_path)
    _record(
        logger, op_id="op-aa", cmd="mail-attach-add",
        drive_id="me", item_id="m1",
        args={"name": "x"},
        before={},
        after={"id": "att-1", "name": "x", "size": 10, "content_hash": "h"},
    )
    rev = build_reverse_mail_operation(logger, "op-aa")
    assert rev.action == "mail.attach.remove"
    assert rev.args["attachment_id"] == "att-1"


def test_reverse_attach_remove_emits_add_from_captured(tmp_path):
    logger = _logger(tmp_path)
    prior = {
        "id": "att-1", "name": "report.pdf",
        "content_type": "application/pdf", "size": 100,
        "content_bytes_b64": "ZGF0YQ==",
    }
    _record(
        logger, op_id="op-ar", cmd="mail-attach-remove",
        drive_id="me", item_id="m1",
        args={"attachment_id": "att-1"}, before=prior, after=None,
    )
    rev = build_reverse_mail_operation(logger, "op-ar")
    assert rev.action == "mail.attach.add"
    assert rev.args["name"] == "report.pdf"
    assert rev.args["content_bytes_b64"] == "ZGF0YQ=="


# ---- Irreversibles ---------------------------------------------------------

def test_dispatcher_mail_send_is_irreversible():
    d = Dispatcher()
    register_mail_inverses(d)
    with pytest.raises(IrreversibleOp) as ei:
        d.build_inverse("mail.send", before={}, after={})
    assert "recalled" in str(ei.value).lower() or "cannot" in str(ei.value).lower()


def test_dispatcher_mail_reply_is_irreversible():
    d = Dispatcher()
    register_mail_inverses(d)
    with pytest.raises(IrreversibleOp):
        d.build_inverse("mail.reply", before={}, after={})


def test_dispatcher_mail_reply_all_is_irreversible():
    d = Dispatcher()
    register_mail_inverses(d)
    with pytest.raises(IrreversibleOp):
        d.build_inverse("mail.reply.all", before={}, after={})


def test_dispatcher_mail_forward_is_irreversible():
    d = Dispatcher()
    register_mail_inverses(d)
    with pytest.raises(IrreversibleOp):
        d.build_inverse("mail.forward", before={}, after={})


def test_dispatcher_registers_all_phase5a_reversibles():
    d = Dispatcher()
    register_mail_inverses(d)
    for action in (
        "mail.draft.create", "mail.draft.update", "mail.draft.delete",
        "mail.attach.add", "mail.attach.remove",
    ):
        assert d.is_registered(action), f"missing reversible {action}"
```

Run: `uv run pytest tests/test_mail_mutate_undo_phase5a.py -q` → all FAIL.

- [ ] **Step 2: Extend `build_reverse_mail_operation`.**

In `src/m365ctl/mail/mutate/undo.py`, after the `mail-delete-soft` branch (Phase 4), before the final raise, add:

```python
    if cmd == "mail-draft-create":
        new_id = after.get("id")
        if not new_id:
            raise Irreversible(
                f"mail-draft-create op {op_id!r} has no after.id; cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.draft.delete",
            drive_id=drive_id, item_id=new_id, args={},
            dry_run_result=f"(undo of {op_id}) delete draft {new_id!r}",
        )

    if cmd == "mail-draft-update":
        if not before:
            raise Irreversible(
                f"mail-draft-update op {op_id!r} has empty before; cannot undo"
            )
        args: dict = {}
        if "subject" in before:
            args["subject"] = before["subject"]
        if "body" in before and isinstance(before["body"], dict):
            args["body"] = before["body"].get("content", "")
            args["body_type"] = before["body"].get("contentType", "text")
        if "toRecipients" in before:
            args["to"] = [r.get("emailAddress", {}).get("address", "")
                          for r in before["toRecipients"]]
        if "ccRecipients" in before:
            args["cc"] = [r.get("emailAddress", {}).get("address", "")
                          for r in before["ccRecipients"]]
        return Operation(
            op_id=new_op_id(), action="mail.draft.update",
            drive_id=drive_id, item_id=start["item_id"],
            args=args,
            dry_run_result=f"(undo of {op_id}) restore draft {start['item_id']!r}",
        )

    if cmd == "mail-draft-delete":
        if not before or "subject" not in before:
            raise Irreversible(
                f"mail-draft-delete op {op_id!r} has no before.subject; "
                f"cannot reconstruct the deleted draft"
            )
        subject = before.get("subject", "")
        body_block = before.get("body", {}) or {}
        args: dict = {
            "subject": subject,
            "body": body_block.get("content", ""),
            "body_type": body_block.get("contentType", "text"),
            "to": [r.get("emailAddress", {}).get("address", "")
                   for r in before.get("toRecipients", []) or []],
        }
        if before.get("ccRecipients"):
            args["cc"] = [r.get("emailAddress", {}).get("address", "")
                          for r in before["ccRecipients"]]
        if before.get("bccRecipients"):
            args["bcc"] = [r.get("emailAddress", {}).get("address", "")
                           for r in before["bccRecipients"]]
        return Operation(
            op_id=new_op_id(), action="mail.draft.create",
            drive_id=drive_id, item_id="", args=args,
            dry_run_result=f"(undo of {op_id}) recreate draft {subject!r}",
        )

    if cmd == "mail-attach-add":
        new_att = after.get("id")
        if not new_att:
            raise Irreversible(
                f"mail-attach-add op {op_id!r} has no after.id; cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.attach.remove",
            drive_id=drive_id, item_id=start["item_id"],
            args={"attachment_id": new_att},
            dry_run_result=f"(undo of {op_id}) remove attachment {new_att!r}",
        )

    if cmd == "mail-attach-remove":
        if not before.get("content_bytes_b64"):
            raise Irreversible(
                f"mail-attach-remove op {op_id!r} has no before.content_bytes_b64; "
                f"cannot recreate the attachment"
            )
        return Operation(
            op_id=new_op_id(), action="mail.attach.add",
            drive_id=drive_id, item_id=start["item_id"],
            args={
                "name": before.get("name", ""),
                "content_type": before.get("content_type", "application/octet-stream"),
                "content_bytes_b64": before["content_bytes_b64"],
            },
            dry_run_result=f"(undo of {op_id}) re-add attachment "
                           f"{before.get('name', '?')!r}",
        )
```

- [ ] **Step 3: Extend `register_mail_inverses` with the 5 reversibles + 4 irreversibles.**

After the existing Phase 2/3/4 registrations, before the function returns, add:
```python
    dispatcher.register("mail.draft.create", lambda b, a: {
        "action": "mail.draft.delete", "args": {},
    })
    dispatcher.register("mail.draft.update", lambda b, a: {
        "action": "mail.draft.update",
        "args": {
            "subject": b.get("subject", ""),
            "body": (b.get("body", {}) or {}).get("content", ""),
            "body_type": (b.get("body", {}) or {}).get("contentType", "text"),
        },
    })
    dispatcher.register("mail.draft.delete", lambda b, a: {
        "action": "mail.draft.create",
        "args": {
            "subject": b.get("subject", ""),
            "body": (b.get("body", {}) or {}).get("content", ""),
            "body_type": (b.get("body", {}) or {}).get("contentType", "text"),
            "to": [r.get("emailAddress", {}).get("address", "")
                   for r in b.get("toRecipients", []) or []],
        },
    })
    dispatcher.register("mail.attach.add", lambda b, a: {
        "action": "mail.attach.remove",
        "args": {"attachment_id": a.get("id", "")},
    })
    dispatcher.register("mail.attach.remove", lambda b, a: {
        "action": "mail.attach.add",
        "args": {
            "name": b.get("name", ""),
            "content_type": b.get("content_type", "application/octet-stream"),
            "content_bytes_b64": b.get("content_bytes_b64", ""),
        },
    })

    dispatcher.register_irreversible(
        "mail.send",
        "Sent mail cannot be recalled programmatically. "
        "If the recipient hasn't opened the message yet, use the Outlook client's "
        "'Recall this message' feature.",
    )
    dispatcher.register_irreversible(
        "mail.reply",
        "Sent replies cannot be recalled programmatically.",
    )
    dispatcher.register_irreversible(
        "mail.reply.all",
        "Sent reply-all messages cannot be recalled programmatically.",
    )
    dispatcher.register_irreversible(
        "mail.forward",
        "Sent forwards cannot be recalled programmatically.",
    )
```

- [ ] **Step 4: Extend `src/m365ctl/mail/cli/undo.py` with Phase 5a executor dispatch.**

Read the file. Find the `elif action == "mail.delete.soft":` branch. After it (but before the final `else`/return-2), add:
```python
    elif action == "mail.draft.create":
        from m365ctl.mail.mutate.draft import execute_create_draft
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_create_draft(rev, graph, logger, before={})

    elif action == "mail.draft.update":
        from m365ctl.mail.mutate.draft import execute_update_draft
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_update_draft(rev, graph, logger, before={})

    elif action == "mail.draft.delete":
        from m365ctl.mail.mutate.draft import execute_delete_draft
        rev.args.setdefault("auth_mode", auth_mode)
        # For the undo of a create, `before` is the Phase 1 empty dict — the delete
        # just removes the newly created draft. The reverse-of-reverse (rebuilding
        # the draft we just deleted) would need the captured body, but since this
        # IS the undo-of-create path, that's acceptable: the reversed create's
        # before was empty anyway.
        r = execute_delete_draft(rev, graph, logger, before={})

    elif action == "mail.attach.add":
        from m365ctl.mail.mutate.attach import execute_add_attachment_small
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_add_attachment_small(rev, graph, logger, before={})

    elif action == "mail.attach.remove":
        from m365ctl.mail.mutate.attach import execute_remove_attachment
        rev.args.setdefault("auth_mode", auth_mode)
        r = execute_remove_attachment(rev, graph, logger, before={})
```

- [ ] **Step 5: Run + commit.**
```bash
uv run pytest tests/test_mail_mutate_undo_phase5a.py tests/test_mail_mutate_undo_phase4.py tests/test_mail_mutate_undo_phase3.py tests/test_mail_mutate_undo.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/mutate/undo.py src/m365ctl/mail/cli/undo.py tests/test_mail_mutate_undo_phase5a.py
git commit -m "feat(mail/mutate): Phase 5a undo — draft/attach reversibles + send/reply/forward irreversibles"
```

Expected: 518 + 11 = 529. Existing Phase 2/3/4 undo tests must still pass.

---

## Group 8: CLIs — mail draft + mail send + mail reply + mail forward + mail attach (write side)

### Task 8: Five new CLI modules

**Files:**
- Create: `src/m365ctl/mail/cli/draft.py`
- Create: `src/m365ctl/mail/cli/send.py`
- Create: `src/m365ctl/mail/cli/reply.py`
- Create: `src/m365ctl/mail/cli/forward.py`
- Modify: `src/m365ctl/mail/cli/attach.py` (Phase 1 has list/get — add `add` + `remove` subcommands)
- Create: `tests/test_cli_mail_draft.py`, `tests/test_cli_mail_send.py`, `tests/test_cli_mail_reply.py`, `tests/test_cli_mail_forward.py`, `tests/test_cli_mail_attach_write.py`

Given the scale, bundle into ONE big commit. Each CLI follows the Phase 3/4 single-item / `--from-plan` shape established in prior phases. Parser tests only for Phase 5a; live-smoke is user-performed.

For brevity, the plan shows the **mail-draft** CLI in full. The other 4 follow the same pattern; implement each in the same style:
- `add_common_args(p)` + `p.add_argument("--confirm", ...)`.
- Single-item args specific to the verb.
- `--from-plan` support for bulk execute.
- Dry-run default; `--confirm` executes.
- `assert_mail_target_allowed` before Graph calls.

### Step 1: `src/m365ctl/mail/cli/draft.py`

```python
"""`m365ctl mail draft {create|update|delete}` — draft lifecycle."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.draft import (
    execute_create_draft,
    execute_delete_draft,
    execute_update_draft,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail draft")
    add_common_args(p)
    p.add_argument("--confirm", action="store_true")
    sub = p.add_subparsers(dest="subcommand", required=True)

    c = sub.add_parser("create", help="Create a new draft.")
    c.add_argument("--subject", default="")
    c.add_argument("--body", help="Body text (inline). Prefer --body-file.")
    c.add_argument("--body-file", help="Path to body content (text or HTML).")
    c.add_argument("--body-type", choices=("text", "html"), default="text")
    c.add_argument("--to", action="append", default=[])
    c.add_argument("--cc", action="append", default=[])
    c.add_argument("--bcc", action="append", default=[])
    c.add_argument("--importance", choices=("low", "normal", "high"))

    u = sub.add_parser("update", help="Update an existing draft.")
    u.add_argument("draft_id")
    u.add_argument("--subject")
    u.add_argument("--body")
    u.add_argument("--body-file")
    u.add_argument("--body-type", choices=("text", "html"))
    u.add_argument("--to", action="append", default=[])
    u.add_argument("--cc", action="append", default=[])
    u.add_argument("--bcc", action="append", default=[])

    d = sub.add_parser("delete", help="Delete a draft.")
    d.add_argument("draft_id")

    return p


def _read_body(args) -> str:
    if args.body_file:
        return Path(args.body_file).read_text()
    return args.body or ""


def _run_create(args) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        print(f"(dry-run) would create draft subject={args.subject!r} to={args.to}",
              file=sys.stderr)
        return 0
    body = _read_body(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    op = Operation(
        op_id=new_op_id(), action="mail.draft.create",
        drive_id=derive_mailbox_upn(args.mailbox), item_id="",
        args={
            "subject": args.subject,
            "body": body,
            "body_type": args.body_type,
            "to": list(args.to),
            "cc": list(args.cc),
            "bcc": list(args.bcc),
            "importance": args.importance,
            "auth_mode": auth_mode,
        },
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_create_draft(op, graph, logger, before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    new_id = (result.after or {}).get("id", "")
    print(f"[{op.op_id}] ok — created draft {new_id}")
    return 0


def _run_update(args) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        print(f"(dry-run) would update draft {args.draft_id}", file=sys.stderr)
        return 0

    call_args: dict = {"auth_mode": auth_mode}
    if args.subject is not None:
        call_args["subject"] = args.subject
    if args.body is not None or args.body_file is not None:
        call_args["body"] = _read_body(args)
        if args.body_type:
            call_args["body_type"] = args.body_type
    if args.to:
        call_args["to"] = list(args.to)
    if args.cc:
        call_args["cc"] = list(args.cc)
    if args.bcc:
        call_args["bcc"] = list(args.bcc)

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    # Fetch current draft for before capture.
    from m365ctl.mail.messages import get_message
    try:
        msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                          message_id=args.draft_id)
        before = {
            "subject": msg.subject,
            "body": {"contentType": (msg.body.content_type if msg.body else "text"),
                     "content": (msg.body.content if msg.body else "")},
        }
    except Exception:
        before = {}

    op = Operation(
        op_id=new_op_id(), action="mail.draft.update",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.draft_id,
        args=call_args,
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_update_draft(op, graph, logger, before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — updated draft {args.draft_id}")
    return 0


def _run_delete(args) -> int:
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        print(f"(dry-run) would delete draft {args.draft_id}", file=sys.stderr)
        return 0

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    # Fetch full draft for before capture so undo can recreate.
    from m365ctl.mail.messages import get_message
    try:
        msg = get_message(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                          message_id=args.draft_id)
        before = {
            "subject": msg.subject,
            "body": {"contentType": (msg.body.content_type if msg.body else "text"),
                     "content": (msg.body.content if msg.body else "")},
            "toRecipients": [
                {"emailAddress": {"address": a.address, "name": a.name}}
                for a in msg.to
            ],
            "ccRecipients": [
                {"emailAddress": {"address": a.address, "name": a.name}}
                for a in msg.cc
            ],
            "bccRecipients": [
                {"emailAddress": {"address": a.address, "name": a.name}}
                for a in msg.bcc
            ],
        }
    except Exception:
        before = {}

    op = Operation(
        op_id=new_op_id(), action="mail.draft.delete",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.draft_id,
        args={"auth_mode": auth_mode},
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_delete_draft(op, graph, logger, before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — deleted draft {args.draft_id}")
    return 0


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.subcommand == "create":
        return _run_create(args)
    if args.subcommand == "update":
        return _run_update(args)
    if args.subcommand == "delete":
        return _run_delete(args)
    return 2
```

### Step 2: `tests/test_cli_mail_draft.py`

```python
import pytest
from m365ctl.mail.cli.draft import build_parser


def test_draft_create_parser():
    args = build_parser().parse_args([
        "create", "--subject", "hi", "--body", "body", "--to", "a@example.com", "--confirm",
    ])
    assert args.subcommand == "create"
    assert args.subject == "hi"
    assert args.body == "body"
    assert args.to == ["a@example.com"]


def test_draft_update_requires_id():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["update"])


def test_draft_update_partial():
    args = build_parser().parse_args(["update", "d1", "--subject", "new"])
    assert args.subcommand == "update"
    assert args.draft_id == "d1"
    assert args.subject == "new"
    assert args.body is None


def test_draft_delete_requires_id():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["delete"])
    args = build_parser().parse_args(["delete", "d1", "--confirm"])
    assert args.subcommand == "delete"
    assert args.draft_id == "d1"
```

### Step 3: `src/m365ctl/mail/cli/send.py`

```python
"""`m365ctl mail send` — send an existing draft OR send inline (if drafts_before_send=false)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, load_plan, new_op_id
from m365ctl.mail.cli._bulk import confirm_bulk_proceed
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.compose import count_external_recipients, parse_recipients
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.send import execute_send_draft, execute_send_new


_EXTERNAL_RECIP_TTY_THRESHOLD = 20


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="m365ctl mail send",
        description="Send an existing draft (by id) OR, if --new, send inline. "
                    "`--new` is blocked when [mail].drafts_before_send is true (default).",
    )
    add_common_args(p)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("draft_id", nargs="?", help="Draft id to send.")
    p.add_argument("--new", action="store_true",
                   help="Send inline (no persistent draft). Blocked when drafts_before_send=true.")
    p.add_argument("--subject")
    p.add_argument("--body")
    p.add_argument("--body-file")
    p.add_argument("--body-type", choices=("text", "html"), default="text")
    p.add_argument("--to", action="append", default=[])
    p.add_argument("--cc", action="append", default=[])
    p.add_argument("--bcc", action="append", default=[])
    p.add_argument("--importance", choices=("low", "normal", "high"))
    p.add_argument("--from-plan")
    return p


def _read_body(args) -> str:
    if args.body_file:
        return Path(args.body_file).read_text()
    return args.body or ""


def _check_external_recipients(to: list[str], cc: list[str], bcc: list[str],
                                internal_domain: str | None) -> int:
    """Return count of external recipients across to/cc/bcc."""
    recips = parse_recipients(to + cc + bcc)
    return count_external_recipients(recips, internal_domain=internal_domain)


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.from_plan:
        if not args.confirm:
            print("mail send --from-plan requires --confirm.", file=sys.stderr)
            return 2
        cfg, auth_mode, cred = load_and_authorize(args)
        plan = load_plan(Path(args.from_plan))
        ops = [op for op in plan.operations if op.action == "mail.send"]
        if not confirm_bulk_proceed(len(ops), verb="send"):
            return 2
        token = cred.get_token()
        graph = GraphClient(token_provider=lambda: token)
        logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
        any_error = False
        for op in ops:
            op.args.setdefault("auth_mode", auth_mode)
            if op.args.get("new"):
                result = execute_send_new(op, graph, logger, before={})
            else:
                result = execute_send_draft(op, graph, logger, before={})
            if result.status != "ok":
                any_error = True
                print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
            else:
                print(f"[{op.op_id}] ok")
        return 1 if any_error else 0

    if args.new:
        cfg, auth_mode, cred = load_and_authorize(args)
        if cfg.mail.drafts_before_send:
            print(
                "mail send --new: blocked by [mail].drafts_before_send=true. "
                "Use `mail draft create` + `mail send <draft-id>` for review-before-send ergonomics, "
                "or set [mail].drafts_before_send=false in config.toml to enable inline send.",
                file=sys.stderr,
            )
            return 2
        assert_mail_target_allowed(
            cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
            unsafe_scope=args.unsafe_scope,
        )
        external = _check_external_recipients(
            args.to, args.cc, args.bcc,
            internal_domain=None,  # Phase 5a: conservative — all external; Phase 7 reads config
        )
        if external > _EXTERNAL_RECIP_TTY_THRESHOLD:
            from m365ctl.common.safety import _confirm_via_tty
            prompt = f"mail send: {external} external recipients. Proceed? [y/N]: "
            if not _confirm_via_tty(prompt):
                print("aborted: user declined /dev/tty confirm.", file=sys.stderr)
                return 2
        if not args.confirm:
            print(f"(dry-run) would send inline to={args.to} subject={args.subject!r}",
                  file=sys.stderr)
            return 0
        body = _read_body(args)
        token = cred.get_token()
        graph = GraphClient(token_provider=lambda: token)
        op = Operation(
            op_id=new_op_id(), action="mail.send",
            drive_id=derive_mailbox_upn(args.mailbox), item_id="",
            args={
                "subject": args.subject or "",
                "body": body,
                "body_type": args.body_type,
                "to": list(args.to),
                "cc": list(args.cc),
                "bcc": list(args.bcc),
                "importance": args.importance,
                "new": True,
                "auth_mode": auth_mode,
            },
        )
        logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
        result = execute_send_new(op, graph, logger, before={})
        if result.status != "ok":
            print(f"error: {result.error}", file=sys.stderr)
            return 1
        print(f"[{op.op_id}] ok — sent")
        return 0

    if not args.draft_id:
        print("mail send: pass draft_id (or --new, or --from-plan --confirm).", file=sys.stderr)
        return 2
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        print(f"(dry-run) would send draft {args.draft_id}", file=sys.stderr)
        return 0
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    op = Operation(
        op_id=new_op_id(), action="mail.send",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.draft_id,
        args={"auth_mode": auth_mode},
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_send_draft(op, graph, logger, before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — sent draft {args.draft_id}")
    return 0
```

### Step 4: `tests/test_cli_mail_send.py`

```python
from m365ctl.mail.cli.send import build_parser


def test_send_parser_draft_id():
    args = build_parser().parse_args(["d1", "--confirm"])
    assert args.draft_id == "d1"
    assert not args.new


def test_send_parser_new_mode():
    args = build_parser().parse_args([
        "--new", "--subject", "hi", "--body", "body",
        "--to", "a@example.com", "--confirm",
    ])
    assert args.new is True
    assert args.subject == "hi"
    assert args.to == ["a@example.com"]


def test_send_parser_from_plan():
    args = build_parser().parse_args(["--from-plan", "/tmp/p.json", "--confirm"])
    assert args.from_plan == "/tmp/p.json"
```

### Step 5: `src/m365ctl/mail/cli/reply.py`

```python
"""`m365ctl mail reply` — reply to a message (create draft OR inline send)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.reply import (
    execute_create_reply,
    execute_create_reply_all,
    execute_send_reply_inline,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail reply")
    add_common_args(p)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("message_id")
    p.add_argument("--all", action="store_true", help="Reply-all instead of reply-to-sender.")
    p.add_argument("--inline", action="store_true",
                   help="Send inline (one-shot) rather than create a draft-reply.")
    p.add_argument("--body", help="Inline body (required with --inline).")
    p.add_argument("--body-file")
    return p


def _read_body(args) -> str:
    if args.body_file:
        return Path(args.body_file).read_text()
    return args.body or ""


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        kind = "reply-all" if args.all else "reply"
        mode = "inline" if args.inline else "create draft"
        print(f"(dry-run) would {kind} ({mode}) to {args.message_id}", file=sys.stderr)
        return 0

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)

    if args.inline:
        if not (args.body or args.body_file):
            print("mail reply --inline requires --body or --body-file.", file=sys.stderr)
            return 2
        body = _read_body(args)
        op = Operation(
            op_id=new_op_id(),
            action="mail.reply.all" if args.all else "mail.reply",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
            args={"mode": "inline", "body": body, "auth_mode": auth_mode},
        )
        result = execute_send_reply_inline(op, graph, logger, before={})
    elif args.all:
        op = Operation(
            op_id=new_op_id(), action="mail.reply.all",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
            args={"mode": "create", "auth_mode": auth_mode},
        )
        result = execute_create_reply_all(op, graph, logger, before={})
    else:
        op = Operation(
            op_id=new_op_id(), action="mail.reply",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
            args={"mode": "create", "auth_mode": auth_mode},
        )
        result = execute_create_reply(op, graph, logger, before={})

    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    if args.inline:
        print(f"[{op.op_id}] ok — sent inline reply")
    else:
        new_draft = (result.after or {}).get("draft_id", "")
        print(f"[{op.op_id}] ok — created draft {new_draft}")
    return 0
```

### Step 6: `tests/test_cli_mail_reply.py`

```python
import pytest
from m365ctl.mail.cli.reply import build_parser


def test_reply_parser_basic():
    args = build_parser().parse_args(["m1", "--confirm"])
    assert args.message_id == "m1"
    assert not args.all
    assert not args.inline


def test_reply_parser_reply_all():
    args = build_parser().parse_args(["m1", "--all", "--confirm"])
    assert args.all is True


def test_reply_parser_inline():
    args = build_parser().parse_args(["m1", "--inline", "--body", "ok", "--confirm"])
    assert args.inline is True
    assert args.body == "ok"


def test_reply_parser_requires_message_id():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
```

### Step 7: `src/m365ctl/mail/cli/forward.py`

```python
"""`m365ctl mail forward` — forward a message (create draft OR inline send)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.forward import execute_create_forward, execute_send_forward_inline


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail forward")
    add_common_args(p)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("message_id")
    p.add_argument("--inline", action="store_true")
    p.add_argument("--body")
    p.add_argument("--body-file")
    p.add_argument("--to", action="append", default=[])
    return p


def _read_body(args) -> str:
    if args.body_file:
        return Path(args.body_file).read_text()
    return args.body or ""


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        mode = "inline" if args.inline else "create draft"
        print(f"(dry-run) would forward ({mode}) {args.message_id} to {args.to}",
              file=sys.stderr)
        return 0

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)

    if args.inline:
        if not args.to:
            print("mail forward --inline requires at least one --to.", file=sys.stderr)
            return 2
        body = _read_body(args)
        op = Operation(
            op_id=new_op_id(), action="mail.forward",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
            args={"mode": "inline", "body": body, "to": list(args.to),
                  "auth_mode": auth_mode},
        )
        result = execute_send_forward_inline(op, graph, logger, before={})
    else:
        op = Operation(
            op_id=new_op_id(), action="mail.forward",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
            args={"mode": "create", "auth_mode": auth_mode},
        )
        result = execute_create_forward(op, graph, logger, before={})

    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    if args.inline:
        print(f"[{op.op_id}] ok — forwarded inline")
    else:
        new_draft = (result.after or {}).get("draft_id", "")
        print(f"[{op.op_id}] ok — created forward-draft {new_draft}")
    return 0
```

### Step 8: `tests/test_cli_mail_forward.py`

```python
import pytest
from m365ctl.mail.cli.forward import build_parser


def test_forward_parser_basic():
    args = build_parser().parse_args(["m1", "--confirm"])
    assert args.message_id == "m1"
    assert not args.inline


def test_forward_parser_inline_with_to():
    args = build_parser().parse_args([
        "m1", "--inline", "--body", "fyi", "--to", "c@example.com", "--confirm",
    ])
    assert args.inline is True
    assert args.to == ["c@example.com"]
```

### Step 9: Modify `src/m365ctl/mail/cli/attach.py` — add write subcommands

Phase 1's `attach.py` has `list` + `get` subparsers. Add two more: `add` + `remove`.

Read the current file first. Locate the `sub = p.add_subparsers(...)` block. Add:
```python
    a = sub.add_parser("add", help="Add an attachment to a message.")
    a.add_argument("message_id")
    a.add_argument("--file", required=True, help="Path to the file to attach.")
    a.add_argument("--content-type", help="MIME type (default: sniff from filename).")
    a.add_argument("--confirm", action="store_true")

    rm = sub.add_parser("remove", help="Remove an attachment from a message.")
    rm.add_argument("message_id")
    rm.add_argument("attachment_id")
    rm.add_argument("--confirm", action="store_true")
```

Extend `main` with branches for `add` + `remove`:
```python
    if args.subcommand == "add":
        return _run_add_attachment(args)
    if args.subcommand == "remove":
        return _run_remove_attachment(args)
```

Implement `_run_add_attachment`:
```python
def _run_add_attachment(args) -> int:
    import base64
    import mimetypes
    from pathlib import Path as _Path
    from m365ctl.mail.mutate.attach import execute_add_attachment_small, pick_upload_strategy

    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )

    file_path = _Path(args.file)
    if not file_path.exists():
        print(f"mail attach add: file not found: {args.file}", file=sys.stderr)
        return 2
    raw = file_path.read_bytes()
    size = len(raw)
    strategy = pick_upload_strategy(size=size)
    if strategy == "large":
        print(
            f"mail attach add: file is {size} bytes (≥ 3 MB). "
            f"Large-attachment upload session arrives in Phase 5a-2. "
            f"For now, split or compress the file.",
            file=sys.stderr,
        )
        return 2

    if not args.confirm:
        print(f"(dry-run) would attach {args.file} ({size} bytes) to {args.message_id}",
              file=sys.stderr)
        return 0

    content_type = args.content_type or mimetypes.guess_type(args.file)[0] or "application/octet-stream"
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    op = Operation(
        op_id=new_op_id(), action="mail.attach.add",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
        args={
            "name": file_path.name,
            "content_type": content_type,
            "content_bytes_b64": base64.b64encode(raw).decode("ascii"),
            "auth_mode": auth_mode,
        },
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_add_attachment_small(op, graph, logger, before={})
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    att_id = (result.after or {}).get("id", "")
    print(f"[{op.op_id}] ok — added attachment {att_id}")
    return 0
```

Implement `_run_remove_attachment`:
```python
def _run_remove_attachment(args) -> int:
    import base64
    from m365ctl.mail.mutate.attach import execute_remove_attachment

    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    if not args.confirm:
        print(f"(dry-run) would remove attachment {args.attachment_id} from {args.message_id}",
              file=sys.stderr)
        return 0

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    # Fetch the attachment first so before captures full bytes (for undo).
    from m365ctl.mail.attachments import get_attachment_content, list_attachments
    try:
        atts = list_attachments(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                                message_id=args.message_id)
        match = next((a for a in atts if a.id == args.attachment_id), None)
        if match is None:
            print(f"mail attach remove: attachment {args.attachment_id} not found on {args.message_id}",
                  file=sys.stderr)
            return 2
        content = get_attachment_content(
            graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
            message_id=args.message_id, attachment_id=args.attachment_id,
        )
        before = {
            "id": match.id,
            "name": match.name,
            "content_type": match.content_type,
            "size": match.size,
            "content_bytes_b64": base64.b64encode(content).decode("ascii"),
        }
    except Exception:
        before = {}

    op = Operation(
        op_id=new_op_id(), action="mail.attach.remove",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
        args={"attachment_id": args.attachment_id, "auth_mode": auth_mode},
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_remove_attachment(op, graph, logger, before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — removed attachment {args.attachment_id}")
    return 0
```

Add the needed imports at the top of `src/m365ctl/mail/cli/attach.py`:
```python
from m365ctl.common.audit import AuditLogger
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
```

### Step 10: `tests/test_cli_mail_attach_write.py`

```python
import pytest
from m365ctl.mail.cli.attach import build_parser


def test_attach_add_parser():
    args = build_parser().parse_args([
        "add", "m1", "--file", "/tmp/x.pdf", "--confirm",
    ])
    assert args.subcommand == "add"
    assert args.message_id == "m1"
    assert args.file == "/tmp/x.pdf"


def test_attach_add_requires_file():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["add", "m1"])


def test_attach_remove_parser():
    args = build_parser().parse_args(["remove", "m1", "att-1", "--confirm"])
    assert args.subcommand == "remove"
    assert args.message_id == "m1"
    assert args.attachment_id == "att-1"
```

### Step 11: Run + commit

```bash
uv run pytest tests/test_cli_mail_draft.py tests/test_cli_mail_send.py tests/test_cli_mail_reply.py tests/test_cli_mail_forward.py tests/test_cli_mail_attach_write.py tests/test_cli_mail_attach.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/cli/draft.py src/m365ctl/mail/cli/send.py src/m365ctl/mail/cli/reply.py src/m365ctl/mail/cli/forward.py src/m365ctl/mail/cli/attach.py tests/test_cli_mail_draft.py tests/test_cli_mail_send.py tests/test_cli_mail_reply.py tests/test_cli_mail_forward.py tests/test_cli_mail_attach_write.py
git commit -m "feat(mail/cli): Phase 5a CLIs — mail draft + send + reply + forward + attach {add,remove}"
```

Expected: 529 + 4 + 3 + 4 + 2 + 3 = 545.

---

## Group 9: Dispatcher routes + bin wrappers

### Task 9: Wire 5 new verbs into dispatcher + ship bin wrappers

**Files:**
- Modify: `src/m365ctl/mail/cli/__main__.py`
- Create: `bin/mail-draft`, `bin/mail-send`, `bin/mail-reply`, `bin/mail-forward`
- (`bin/mail-attach` already exists from Phase 1.)

- [ ] **Step 1: Dispatcher routing.**

Read `src/m365ctl/mail/cli/__main__.py`. Find the `elif verb == "delete":` branch (Phase 4 G3). After it, add:
```python
    elif verb == "draft":
        from m365ctl.mail.cli.draft import main as f
    elif verb == "send":
        from m365ctl.mail.cli.send import main as f
    elif verb == "reply":
        from m365ctl.mail.cli.reply import main as f
    elif verb == "forward":
        from m365ctl.mail.cli.forward import main as f
```

(`attach` already has a dispatcher entry from Phase 1 — the list/get handler now also handles add/remove via subparsers.)

Update `_USAGE` to include the new compose verbs. Find the "Mutations (safe — all undoable):" section and add:
```
  draft        create/update/delete drafts (undoable)
  send         send draft or inline (IRREVERSIBLE)
  reply        reply to a message (IRREVERSIBLE — inline send)
  forward      forward a message (IRREVERSIBLE — inline send)
```

- [ ] **Step 2: Create 4 bin wrappers.**

```bash
for verb in draft send reply forward; do
  cat > "bin/mail-$verb" <<EOF
#!/usr/bin/env bash
set -euo pipefail
REPO="\$(cd "\$(dirname "\$0")/.." && pwd)"
exec uv run --project "\$REPO" python -m m365ctl mail $verb "\$@"
EOF
  chmod +x "bin/mail-$verb"
done
```

- [ ] **Step 3: Smoke.**

```bash
uv run python -m m365ctl mail --help | grep -E "draft|send|reply|forward"
for v in draft send reply forward; do echo "--- $v ---"; "./bin/mail-$v" --help 2>&1 | head -3; done
./bin/mail-attach add --help 2>&1 | head -3
```

All exit 0.

- [ ] **Step 4: Commit.**
```bash
git add src/m365ctl/mail/cli/__main__.py bin/mail-draft bin/mail-send bin/mail-reply bin/mail-forward
git commit -m "feat(mail/cli): route 4 Phase 5a verbs in dispatcher + bin wrappers"
```

---

## Group 10: Release 0.6.0 + push/PR/merge

### Task 10: Bump + CHANGELOG + plan file + gates + push/PR/merge

- [ ] **Step 1: Bump `pyproject.toml` 0.5.0 → 0.6.0.**

- [ ] **Step 2: CHANGELOG entry** above `[0.5.0]`:

```markdown
## [0.6.0] — 2026-04-25

### Added
- **Mail compose (Phase 5a).** Drafts + send + reply + forward + attachment write-side.
  - `m365ctl mail draft {create,update,delete}` — full draft lifecycle. All undoable (draft.create ↔ draft.delete; draft.update restores prior fields; draft.delete recreates from captured body).
  - `m365ctl mail send <draft-id>` — send an existing draft.
  - `m365ctl mail send --new --subject ... --body-file ... --to ...` — inline send. **Blocked when `[mail].drafts_before_send=true` (default)**; set to false in config to enable.
  - `m365ctl mail send --from-plan plan.json --confirm` — bulk send from a plan file. Bulk ≥20 → `/dev/tty` confirm.
  - `m365ctl mail reply <msg-id>` — creates a draft-reply; `--all` for reply-all; `--inline --body "..."` for one-shot send.
  - `m365ctl mail forward <msg-id>` — creates a draft-forward; `--inline --body "..." --to ...` for one-shot send.
  - `m365ctl mail attach add <msg-id> --file <path>` / `remove <msg-id> <att-id>` — small attachments (<3 MB). Large attachments (≥3 MB) detect + defer to Phase 5a-2 with a clear error.
- `src/m365ctl/mail/compose.py` — pure helpers: `parse_recipients`, `build_message_payload`, `count_external_recipients`.
- 5 new executor modules under `src/m365ctl/mail/mutate/`: `draft.py`, `send.py`, `reply.py`, `forward.py`, `attach.py` (write side).
- **`mail-send` with >20 external recipients → interactive `/dev/tty` confirm** (non-bypassable).
- `bin/mail-draft`, `bin/mail-send`, `bin/mail-reply`, `bin/mail-forward` short wrappers.

### Changed
- `mail/mutate/undo.py`: +5 new reverse-op builders (`mail.draft.{create,update,delete}`, `mail.attach.{add,remove}`); +4 `register_irreversible` calls for `mail.send`, `mail.reply`, `mail.reply.all`, `mail.forward` with operator-facing guidance (e.g. "Sent mail cannot be recalled programmatically").
- `mail/cli/undo.py`: 5 new executor dispatch branches for Phase 5a reversibles.
- `mail/cli/attach.py`: Phase 1's read-only list/get CLI grows `add` + `remove` subcommands.

### Safety
- `--confirm` required for every mutation; dry-run default.
- `mail.send`/`mail.reply*`/`mail.forward` are **irreversible** — clearly surfaced in Dispatcher rejection messages.
- `[mail].drafts_before_send` (default true) blocks `mail send --new` to enforce draft-first review workflow.
- External-recipient TTY confirm on >20 recipients.

### Deferred
- Large attachment upload session (chunked ≥3 MB) → Phase 5a-2.
- Scheduled send (`--schedule-at`) → Phase 5b.
- `internet_message_id` backfill in `after.internet_message_id` → Phase 7 catalog (Graph's 202 response has no body).
- Automatic ETag 412 → refresh → retry loop → Phase 3.5 or later.
```

- [ ] **Step 3: Commit release.**
```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore(release): bump to 0.6.0 + CHANGELOG entry for mail compose (drafts/send/reply/forward/attach)"
```

- [ ] **Step 4: Commit plan file.**
```bash
git add docs/superpowers/plans/2026-04-25-phase-5a-mail-compose.md
git commit -m "docs(plans): commit Phase 5a compose plan"
```

- [ ] **Step 5: Final gates.**

```bash
uv run pytest -m "not live" -q 2>&1 | tail -3
uv run ruff check 2>&1 | tail -5
uv run mypy src 2>&1 | tail -10
```

Expected: 545 passed, 1 deselected. Ruff clean (auto-fix unused imports if any). Mypy baseline ~71 from Phase 4 — record current count (likely +6–10 from 6 new mutate modules + 5 CLIs).

CLI `--help` smokes:
```bash
uv run python -m m365ctl mail draft --help
uv run python -m m365ctl mail send --help
uv run python -m m365ctl mail reply --help
uv run python -m m365ctl mail forward --help
uv run python -m m365ctl mail attach --help
for v in draft send reply forward; do ./bin/mail-$v --help 2>&1 | head -3; done
```
All exit 0.

- [ ] **Step 6: Push + PR + merge.**

```bash
git push -u origin phase-5a-mail-compose
gh pr create --title "Phase 5a: mail compose — drafts/send/reply/forward/attach (0.6.0)" --body "..."
gh pr checks <N> --watch
gh pr merge <N> --merge --delete-branch
git checkout main && git pull
```

### User-performed live smoke (after merge)

```bash
# Draft lifecycle + undo round-trip
./bin/mail-draft create --subject "Test" --body "hi" --to me@mydomain.com --confirm
./bin/mail-draft update <draft-id> --subject "Test (edited)" --confirm
./bin/mail-draft delete <draft-id> --confirm
./bin/m365ctl-undo <delete-op-id> --confirm    # recreates the draft

# Send
./bin/mail-send <draft-id> --confirm
# (irreversible; do not test against production addresses)

# Reply (draft-create is safe to experiment with)
./bin/mail-reply <msg-id> --confirm
# Then delete the draft:
./bin/mail-draft delete <reply-draft-id> --confirm

# Attachment round-trip
./bin/mail-attach add <msg-id> --file /tmp/small.pdf --confirm
./bin/m365ctl-undo <add-op-id> --confirm     # removes the attachment
```

---

## Self-review

**1. Spec coverage (spec §19 Phase 5a deliverables):**
- [x] `mail.compose`: create_draft/update_draft/send_draft/send_new — Tasks 3 + 4.
- [x] create_reply/create_reply_all/send_reply_inline — Task 5.
- [x] create_forward — Task 5.
- [x] `mail.attachments` write side — add_small + remove — Task 6. Large upload session deferred to 5a-2 (explicitly flagged).
- [x] CLIs — Task 8.
- [x] `--body-file` preferred; `--body` inline — Task 8 (each CLI's `_read_body` helper).
- [x] `mail-send --confirm` required + >20 external TTY confirm — Task 8 send.py.
- [x] Compose ops register as `IrreversibleOp` — Task 7.
- [x] `mail.send` records sent_at — Tasks 4 + 5 (internet_message_id empty; Phase 7 backfills).
- [x] Bump to 0.6.0 — Task 10.

**2. Acceptance (spec §19 Phase 5a):**
- Draft create/update/delete round-trip — Task 3 executor + Task 8 CLI; undo tests in Task 7.
- `mail-send <draft-id> --confirm` delivers — Task 4 + Task 8.
- `mail-reply --inline --body "ok" --confirm` one-step — Task 5 + Task 8.
- `mail-attach add <msg> --file 10mb.bin` uses upload session — **partial**: detects size ≥ 3 MB and returns a clean error directing to Phase 5a-2. Full upload session support deferred.
- Plan-based send idempotency test — **deferred**: send-idempotency via `after.internet_message_id` skipping needs Phase 7 catalog to backfill reliably; Phase 5a surfaces `sent_at` but not the message id.

**3. Placeholder scan:** No "TBD" / "implement later" in the plan body outside the explicit deferral notes (5a-2 large upload, Phase 7 catalog backfill).

**4. Type consistency:**
- `execute_<verb>(op, graph, logger, *, before) -> MailResult` — consistent across all 8 new executors.
- `op.args["auth_mode"]` default "delegated" — consistent.
- `parse_recipients([...]) -> list[dict]` → `[{"emailAddress": {...}}]` — consistent across compose.py consumers.
- Dispatcher inverse lambda `(before, after) -> dict` — consistent with Phases 2–4 pattern.
- `pick_upload_strategy(size=int) -> Literal["small", "large"]` — matches test assertions + CLI usage.

---

Plan complete.
