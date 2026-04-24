# Phase 1 — Mail Readers + Auth Scope Expansion

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a complete read-only Microsoft 365 Mail surface on top of the Phase 0 scaffold: list, get, search, threads, folders, categories, rules, settings, attachments. No writes — mutations land in Phase 2+. Every mailbox access gated by `allow_mailboxes`; hard-coded deny list blocks compliance/calendar folders. Bumps version to 0.2.0.

**Architecture:**
- New domain tree `src/m365ctl/mail/`: `endpoints.py`, `models.py`, `messages.py`, `folders.py`, `categories.py`, `rules.py`, `settings.py`, `attachments.py`, plus `cli/` sub-package mirroring the OneDrive CLI shape.
- `GraphClient` is unchanged — we use its existing `get` / `post` / `get_paginated` methods.
- Auth: delegated flow uses `/me/...`; app-only uses `/users/{upn}/...`. A single `user_base(mailbox, auth_mode)` helper picks the right prefix per call.
- Safety: `m365ctl.common.safety.assert_mailbox_allowed(mailbox_upn, cfg, *, auth_mode, unsafe_scope)` + `is_folder_denied(path)`. Drive-side scope primitives stay intact.
- CLI: `m365ctl mail <verb>` (new sub-dispatcher). Short `bin/mail-<verb>` wrappers for muscle memory.
- Tests: unit-level parser/model tests, mocked-Graph integration tests (HTTP recorded via `httpx.MockTransport`), live smoke gated by `M365CTL_LIVE_TESTS=1`.

**Tech Stack:** Python 3.11+ stdlib (dataclasses, typing, datetime), httpx, msal (unchanged). `pytest` for tests, `pytest-mock` for transport stubbing.

**Parent specs:** `docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md` (Phase 1 section — §19), §6 (auth scopes), §7.2 (config), §8 (models), §9.1 (reader endpoints), §10.1–10.2 (CLI flags), §11 (safety), §16 (testing), §20 Q1 (signature endpoint caveats).

**Safety posture:**
- Every mutation → dry-run → plan-file path stays unused (no mutations in this phase).
- `assert_mailbox_allowed` fails closed: any mailbox not in `allow_mailboxes` raises `ScopeViolation` before any Graph request.
- Hard-coded deny folders (`Recoverable Items/*`, `Purges/*`, `Audits/*`, `Calendar/*`, `Contacts/*`, `Tasks/*`, `Notes/*`) are absolute — readers filter them out of results even when the user requests those paths explicitly.
- No force-push; no `git add -A` sweep without reviewing staged diff.
- Work on feature branch `phase-1-mail-readers` (off `main`).

---

## File Structure (Phase 1 target)

```
m365ctl/
├── pyproject.toml                         # MODIFIED — version 0.2.0
├── CHANGELOG.md                           # MODIFIED — [0.2.0] entry
├── bin/
│   ├── mail-auth                          # NEW — delegates to `m365ctl mail auth`
│   ├── mail-whoami                        # NEW — delegates to `m365ctl mail whoami`
│   ├── mail-list                          # NEW
│   ├── mail-get                           # NEW
│   ├── mail-search                        # NEW
│   ├── mail-folders                       # NEW
│   ├── mail-categories                    # NEW
│   ├── mail-rules                         # NEW
│   ├── mail-settings                      # NEW
│   └── mail-attach                        # NEW
├── src/m365ctl/
│   ├── common/
│   │   ├── auth.py                        # MODIFIED — GRAPH_SCOPES_DELEGATED += 3
│   │   └── safety.py                      # MODIFIED — assert_mailbox_allowed, is_folder_denied
│   ├── mail/                              # filled from Phase 0 scaffolds
│   │   ├── __init__.py                    # unchanged
│   │   ├── endpoints.py                   # NEW — user_base(mailbox, auth_mode)
│   │   ├── models.py                      # NEW — dataclasses + from_graph_json parsers
│   │   ├── messages.py                    # NEW — list_messages, get_message, search_messages_graph, get_thread
│   │   ├── folders.py                     # NEW — list_folders, resolve_folder_path, get_folder
│   │   ├── categories.py                  # NEW — list_master_categories
│   │   ├── rules.py                       # NEW — list_rules, get_rule
│   │   ├── settings.py                    # NEW — get_settings, get_auto_reply
│   │   ├── attachments.py                 # NEW — list_attachments, get_attachment
│   │   └── cli/
│   │       ├── __init__.py                # unchanged
│   │       ├── __main__.py                # NEW — mail domain dispatcher
│   │       ├── _common.py                 # NEW — --mailbox / --folder / --json / output helpers
│   │       ├── auth.py                    # NEW — mail-auth (aliases od-auth; same cache)
│   │       ├── whoami.py                  # NEW — mail-whoami (identity, scopes, mailbox access, catalog stub)
│   │       ├── list.py                    # NEW — mail-list
│   │       ├── get.py                     # NEW — mail-get
│   │       ├── search.py                  # NEW — mail-search (server-side; --local deferred to Phase 7)
│   │       ├── folders.py                 # NEW — mail-folders (readers; --tree, --with-counts, --include-hidden)
│   │       ├── categories.py              # NEW — mail-categories (list)
│   │       ├── rules.py                   # NEW — mail-rules (list, show)
│   │       ├── settings.py                # NEW — mail-settings (show)
│   │       └── attach.py                  # NEW — mail-attach (list, get)
│   └── cli/
│       └── __main__.py                    # MODIFIED — `mail` domain routed to mail.cli.__main__
└── tests/
    ├── test_mail_endpoints.py             # NEW
    ├── test_mail_models.py                # NEW
    ├── test_mail_safety.py                # NEW — assert_mailbox_allowed, is_folder_denied
    ├── test_mail_messages.py              # NEW
    ├── test_mail_folders.py               # NEW
    ├── test_mail_categories.py            # NEW
    ├── test_mail_rules.py                 # NEW
    ├── test_mail_settings.py              # NEW
    ├── test_mail_attachments.py           # NEW
    ├── test_cli_mail_whoami.py            # NEW
    ├── test_cli_mail_list.py              # NEW
    ├── test_cli_mail_get.py               # NEW
    ├── test_cli_mail_search.py            # NEW
    ├── test_cli_mail_folders.py           # NEW
    ├── test_cli_mail_categories.py        # NEW
    ├── test_cli_mail_rules.py             # NEW
    ├── test_cli_mail_settings.py          # NEW
    └── test_cli_mail_attach.py            # NEW
```

---

## Preflight

### Task 0: Branch + baseline

**Files:** none (git state).

- [ ] **Step 1: Confirm clean working tree on `main`.**

Run: `git status`  → expected: `nothing to commit, working tree clean`.

- [ ] **Step 2: Create Phase 1 branch.**

Run: `git checkout -b phase-1-mail-readers`

- [ ] **Step 3: Capture baseline.**

Run: `uv run pytest -m "not live" -q 2>&1 | tail -3`
Expected: **246 passed, 1 deselected**. This number is the floor for every subsequent Task.

---

## Group 1: Auth scope expansion + endpoints + models

### Task 1: Add mail scopes to GRAPH_SCOPES_DELEGATED

**Files:**
- Modify: `src/m365ctl/common/auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Failing test — confirm scope list contains the three mail permissions.**

Append to `tests/test_auth.py`:
```python
def test_graph_scopes_delegated_includes_mail_surface():
    from m365ctl.common.auth import GRAPH_SCOPES_DELEGATED
    for required in ("Mail.ReadWrite", "Mail.Send", "MailboxSettings.ReadWrite"):
        assert required in GRAPH_SCOPES_DELEGATED, f"missing delegated scope {required!r}"
```

Run: `uv run pytest tests/test_auth.py::test_graph_scopes_delegated_includes_mail_surface -q`
Expected: FAIL — the scopes aren't present yet.

- [ ] **Step 2: Extend `GRAPH_SCOPES_DELEGATED`.**

In `src/m365ctl/common/auth.py`, locate the existing list:
```python
GRAPH_SCOPES_DELEGATED = [
    "Files.ReadWrite.All",
    "Sites.ReadWrite.All",
    "User.Read",
]
```

Change to:
```python
GRAPH_SCOPES_DELEGATED = [
    "Files.ReadWrite.All",
    "Sites.ReadWrite.All",
    "User.Read",
    # Phase 1 — mail readers + future mutations.
    "Mail.ReadWrite",
    "Mail.Send",
    "MailboxSettings.ReadWrite",
]
```

- [ ] **Step 3: Verify test passes + full suite still green.**

Run: `uv run pytest tests/test_auth.py -q`  → all pass.
Run: `uv run pytest -m "not live" -q 2>&1 | tail -3`  → 247 passed.

- [ ] **Step 4: Commit.**

```bash
git add src/m365ctl/common/auth.py tests/test_auth.py
git commit -m "feat(auth): add Mail.ReadWrite, Mail.Send, MailboxSettings.ReadWrite to delegated scopes"
```

---

### Task 2: `mail/endpoints.py` — `user_base()` helper

**Files:**
- Create: `src/m365ctl/mail/endpoints.py`
- Test: `tests/test_mail_endpoints.py`

- [ ] **Step 1: Failing tests.**

Write `tests/test_mail_endpoints.py`:
```python
"""Unit tests for m365ctl.mail.endpoints."""
from __future__ import annotations

import pytest

from m365ctl.mail.endpoints import (
    AuthMode,
    InvalidMailboxSpec,
    user_base,
    parse_mailbox_spec,
)


def test_user_base_me_delegated():
    assert user_base("me", auth_mode="delegated") == "/me"


def test_user_base_upn_app_only():
    assert user_base("upn:alice@example.com", auth_mode="app-only") == "/users/alice@example.com"


def test_user_base_shared_delegated():
    # Shared mailbox path (delegated still uses /users/ because /me won't work for shared).
    assert user_base("shared:team@example.com", auth_mode="delegated") == "/users/team@example.com"


def test_user_base_rejects_star_wildcard():
    with pytest.raises(InvalidMailboxSpec):
        user_base("*", auth_mode="delegated")


def test_user_base_rejects_me_under_app_only():
    with pytest.raises(InvalidMailboxSpec):
        # app-only has no "signed-in user" — refuse "me".
        user_base("me", auth_mode="app-only")


def test_user_base_rejects_upn_under_delegated_without_delegation():
    # Delegated flow can target /me or a shared mailbox, but not an arbitrary UPN
    # (delegation requires Exchange mailbox permission beyond Graph's Mail.ReadWrite).
    # Phase 1 treats upn: under delegated as explicit-opt-in via the call site;
    # the helper itself permits it.
    assert user_base("upn:bob@example.com", auth_mode="delegated") == "/users/bob@example.com"


@pytest.mark.parametrize("spec,expected", [
    ("me", ("me", None)),
    ("upn:alice@example.com", ("upn", "alice@example.com")),
    ("shared:ops@example.com", ("shared", "ops@example.com")),
    ("*", ("*", None)),
])
def test_parse_mailbox_spec_shapes(spec, expected):
    assert parse_mailbox_spec(spec) == expected


def test_parse_mailbox_spec_rejects_garbage():
    with pytest.raises(InvalidMailboxSpec):
        parse_mailbox_spec("random-text-no-colon")
```

Run: `uv run pytest tests/test_mail_endpoints.py -q`  → all FAIL (module missing).

- [ ] **Step 2: Implement `src/m365ctl/mail/endpoints.py`.**

```python
"""Resolve a `--mailbox` spec to the Graph URL prefix.

Mailbox specs follow the forms documented in spec §11.1:

- ``me``                         — signed-in user (delegated only)
- ``upn:user@example.com``       — specific mailbox (app-only, or delegated with delegation)
- ``shared:team@example.com``    — shared mailbox (either auth mode)
- ``*``                          — wildcard (app-only only; never resolvable by this helper)

`user_base("me", auth_mode="delegated")`         → ``/me``
`user_base("upn:alice@x", auth_mode="app-only")` → ``/users/alice@x``
`user_base("shared:team@x", auth_mode=…)`        → ``/users/team@x``  (Graph treats shared mailboxes as regular user resources)
"""
from __future__ import annotations

from typing import Literal

AuthMode = Literal["delegated", "app-only"]


class InvalidMailboxSpec(ValueError):
    """Raised when a mailbox spec can't be resolved to a Graph URL prefix."""


def parse_mailbox_spec(spec: str) -> tuple[str, str | None]:
    """Split a mailbox spec into ``(kind, address)``.

    Returns:
        ("me", None), ("*", None), ("upn", "<addr>"), or ("shared", "<addr>").

    Raises:
        InvalidMailboxSpec: on malformed input.
    """
    if spec == "me":
        return ("me", None)
    if spec == "*":
        return ("*", None)
    if spec.startswith("upn:"):
        addr = spec[len("upn:"):].strip()
        if not addr or "@" not in addr:
            raise InvalidMailboxSpec(f"upn: spec requires an email address, got {spec!r}")
        return ("upn", addr)
    if spec.startswith("shared:"):
        addr = spec[len("shared:"):].strip()
        if not addr or "@" not in addr:
            raise InvalidMailboxSpec(f"shared: spec requires an email address, got {spec!r}")
        return ("shared", addr)
    raise InvalidMailboxSpec(
        f"unrecognized mailbox spec {spec!r}; expected one of 'me', 'upn:<addr>', 'shared:<addr>', '*'"
    )


def user_base(spec: str, *, auth_mode: AuthMode) -> str:
    """Return the Graph URL prefix (``/me`` or ``/users/{upn}``) for a mailbox spec.

    Raises InvalidMailboxSpec for ``*`` (caller must enumerate) or for ``me`` under app-only.
    """
    kind, addr = parse_mailbox_spec(spec)
    if kind == "*":
        raise InvalidMailboxSpec("wildcard '*' cannot be resolved to a single URL prefix")
    if kind == "me":
        if auth_mode == "app-only":
            raise InvalidMailboxSpec("'me' is not valid under app-only auth; pass 'upn:<addr>' instead")
        return "/me"
    # upn: or shared: — both become /users/{addr}.
    assert addr is not None
    return f"/users/{addr}"
```

Run: `uv run pytest tests/test_mail_endpoints.py -q`  → all PASS.
Run full suite:  `uv run pytest -m "not live" -q 2>&1 | tail -3`  → 254 passed (+7 new).

- [ ] **Step 3: Commit.**

```bash
git add src/m365ctl/mail/endpoints.py tests/test_mail_endpoints.py
git commit -m "feat(mail): endpoints.user_base + parse_mailbox_spec for /me vs /users/{upn} routing"
```

---

### Task 3: `mail/models.py` — dataclasses + Graph-JSON parsers

**Files:**
- Create: `src/m365ctl/mail/models.py`
- Test: `tests/test_mail_models.py`

- [ ] **Step 1: Failing test for `EmailAddress.from_graph_json`.**

Write `tests/test_mail_models.py` with the following (start here; we add more tests after each helper lands):
```python
"""Unit tests for m365ctl.mail.models dataclasses + Graph-JSON parsers."""
from __future__ import annotations

from datetime import datetime, timezone

from m365ctl.mail.models import (
    Attachment,
    AutomaticRepliesSetting,
    Body,
    Category,
    EmailAddress,
    Flag,
    Folder,
    LocaleInfo,
    MailboxSettings,
    Message,
    Rule,
    WorkingHours,
)


# ---- EmailAddress ----------------------------------------------------------

def test_email_address_from_graph_json_full():
    raw = {"emailAddress": {"name": "Alice Example", "address": "alice@example.com"}}
    addr = EmailAddress.from_graph_json(raw)
    assert addr == EmailAddress(name="Alice Example", address="alice@example.com")


def test_email_address_from_graph_json_missing_name():
    raw = {"emailAddress": {"address": "bot@example.com"}}
    addr = EmailAddress.from_graph_json(raw)
    assert addr.name == ""
    assert addr.address == "bot@example.com"


def test_email_address_from_graph_json_accepts_flat_shape():
    # Graph sometimes returns the address directly (e.g. in `sender`).
    raw = {"name": "Bob", "address": "bob@example.com"}
    addr = EmailAddress.from_graph_json(raw)
    assert addr == EmailAddress(name="Bob", address="bob@example.com")


# ---- Body ------------------------------------------------------------------

def test_body_from_graph_json_text():
    raw = {"contentType": "text", "content": "hello"}
    body = Body.from_graph_json(raw)
    assert body == Body(content_type="text", content="hello")


def test_body_from_graph_json_html_stripped():
    raw = {"contentType": "html", "content": "<p>hi</p>"}
    body = Body.from_graph_json(raw)
    assert body.content_type == "html"
    assert body.content == "<p>hi</p>"


# ---- Flag ------------------------------------------------------------------

def test_flag_from_graph_json_not_flagged():
    raw = {"flagStatus": "notFlagged"}
    flag = Flag.from_graph_json(raw)
    assert flag.status == "notFlagged"
    assert flag.start_at is None
    assert flag.due_at is None
    assert flag.completed_at is None


def test_flag_from_graph_json_flagged_with_dates():
    raw = {
        "flagStatus": "flagged",
        "startDateTime": {"dateTime": "2026-04-24T09:00:00.0000000", "timeZone": "UTC"},
        "dueDateTime": {"dateTime": "2026-04-30T17:00:00.0000000", "timeZone": "UTC"},
    }
    flag = Flag.from_graph_json(raw)
    assert flag.status == "flagged"
    assert flag.start_at == datetime(2026, 4, 24, 9, 0, tzinfo=timezone.utc)
    assert flag.due_at == datetime(2026, 4, 30, 17, 0, tzinfo=timezone.utc)


# ---- Folder ----------------------------------------------------------------

def test_folder_from_graph_json():
    raw = {
        "id": "AAMkAD...=",
        "displayName": "Inbox",
        "parentFolderId": "AAMkAD...parent=",
        "totalItemCount": 42,
        "unreadItemCount": 3,
        "childFolderCount": 5,
        "wellKnownName": "inbox",
    }
    f = Folder.from_graph_json(raw, mailbox_upn="me", path="/Inbox")
    assert f.id == "AAMkAD...="
    assert f.display_name == "Inbox"
    assert f.parent_id == "AAMkAD...parent="
    assert f.path == "/Inbox"
    assert f.total_items == 42
    assert f.unread_items == 3
    assert f.child_folder_count == 5
    assert f.well_known_name == "inbox"
    assert f.mailbox_upn == "me"


def test_folder_from_graph_json_defaults():
    raw = {"id": "1", "displayName": "X"}
    f = Folder.from_graph_json(raw, mailbox_upn="me", path="/X")
    assert f.parent_id is None
    assert f.total_items == 0
    assert f.unread_items == 0
    assert f.child_folder_count == 0
    assert f.well_known_name is None


# ---- Category --------------------------------------------------------------

def test_category_from_graph_json():
    raw = {"id": "cat-id", "displayName": "Follow up", "color": "preset0"}
    assert Category.from_graph_json(raw) == Category(
        id="cat-id", display_name="Follow up", color="preset0"
    )


# ---- Rule ------------------------------------------------------------------

def test_rule_from_graph_json():
    raw = {
        "id": "rule-id",
        "displayName": "Archive newsletters",
        "sequence": 10,
        "isEnabled": True,
        "hasError": False,
        "isReadOnly": False,
        "conditions": {"senderContains": ["@news.example.com"]},
        "actions": {"moveToFolder": "AAMkAD...=="},
        "exceptions": {},
    }
    r = Rule.from_graph_json(raw)
    assert r.id == "rule-id"
    assert r.display_name == "Archive newsletters"
    assert r.sequence == 10
    assert r.is_enabled is True
    assert r.has_error is False
    assert r.is_read_only is False
    assert r.conditions == {"senderContains": ["@news.example.com"]}
    assert r.actions == {"moveToFolder": "AAMkAD...=="}
    assert r.exceptions == {}


# ---- Attachment ------------------------------------------------------------

def test_attachment_from_graph_json_file():
    raw = {
        "id": "att-id",
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": "report.pdf",
        "contentType": "application/pdf",
        "size": 12345,
        "isInline": False,
        "contentId": None,
    }
    a = Attachment.from_graph_json(raw, message_id="msg-1")
    assert a.id == "att-id"
    assert a.kind == "file"
    assert a.name == "report.pdf"
    assert a.content_type == "application/pdf"
    assert a.size == 12345
    assert a.is_inline is False
    assert a.content_id is None
    assert a.message_id == "msg-1"


def test_attachment_from_graph_json_item():
    raw = {
        "id": "att2",
        "@odata.type": "#microsoft.graph.itemAttachment",
        "name": "Meeting.ics",
        "contentType": "application/octet-stream",
        "size": 2048,
        "isInline": False,
    }
    a = Attachment.from_graph_json(raw, message_id="msg-2")
    assert a.kind == "item"


def test_attachment_from_graph_json_reference():
    raw = {
        "id": "att3",
        "@odata.type": "#microsoft.graph.referenceAttachment",
        "name": "link.url",
        "contentType": "application/octet-stream",
        "size": 0,
        "isInline": False,
    }
    a = Attachment.from_graph_json(raw, message_id="msg-3")
    assert a.kind == "reference"


# ---- Message ---------------------------------------------------------------

def test_message_from_graph_json_minimal():
    raw = {
        "id": "msg-id",
        "internetMessageId": "<abc@example.com>",
        "conversationId": "conv-id",
        "conversationIndex": "AQ==",  # base64 "\x01"
        "parentFolderId": "folder-id",
        "subject": "Hello",
        "sender": {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
        "from": {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
        "toRecipients": [
            {"emailAddress": {"name": "Bob", "address": "bob@example.com"}}
        ],
        "ccRecipients": [],
        "bccRecipients": [],
        "replyTo": [],
        "receivedDateTime": "2026-04-24T10:00:00Z",
        "sentDateTime": "2026-04-24T09:59:55Z",
        "isRead": False,
        "isDraft": False,
        "hasAttachments": False,
        "importance": "normal",
        "flag": {"flagStatus": "notFlagged"},
        "categories": [],
        "inferenceClassification": "focused",
        "bodyPreview": "Hi...",
        "webLink": "https://outlook.office.com/?ItemID=AAMk...",
        "changeKey": "CQAAABYA...",
    }
    m = Message.from_graph_json(raw, mailbox_upn="me", parent_folder_path="/Inbox")
    assert m.id == "msg-id"
    assert m.internet_message_id == "<abc@example.com>"
    assert m.conversation_id == "conv-id"
    assert m.conversation_index == b"\x01"
    assert m.parent_folder_id == "folder-id"
    assert m.parent_folder_path == "/Inbox"
    assert m.subject == "Hello"
    assert m.sender == EmailAddress(name="Alice", address="alice@example.com")
    assert m.to == [EmailAddress(name="Bob", address="bob@example.com")]
    assert m.cc == []
    assert m.received_at == datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc)
    assert m.sent_at == datetime(2026, 4, 24, 9, 59, 55, tzinfo=timezone.utc)
    assert m.is_read is False
    assert m.is_draft is False
    assert m.has_attachments is False
    assert m.importance == "normal"
    assert m.flag.status == "notFlagged"
    assert m.categories == []
    assert m.inference_classification == "focused"
    assert m.body_preview == "Hi..."
    assert m.body is None
    assert m.web_link.startswith("https://outlook.office.com/")
    assert m.change_key == "CQAAABYA..."
    assert m.mailbox_upn == "me"


def test_message_from_graph_json_with_body_and_attachments():
    raw = {
        "id": "msg",
        "internetMessageId": "<x>",
        "conversationId": "c",
        "conversationIndex": "AQ==",
        "parentFolderId": "f",
        "subject": "Body test",
        "sender": {"emailAddress": {"name": "A", "address": "a@x"}},
        "from": {"emailAddress": {"name": "A", "address": "a@x"}},
        "toRecipients": [],
        "ccRecipients": [],
        "bccRecipients": [],
        "replyTo": [],
        "receivedDateTime": "2026-04-24T10:00:00Z",
        "sentDateTime": None,
        "isRead": True,
        "isDraft": False,
        "hasAttachments": True,
        "importance": "high",
        "flag": {"flagStatus": "notFlagged"},
        "categories": ["Followup"],
        "inferenceClassification": "other",
        "bodyPreview": "p",
        "body": {"contentType": "html", "content": "<p>hi</p>"},
        "webLink": "https://x",
        "changeKey": "ck",
    }
    m = Message.from_graph_json(raw, mailbox_upn="me", parent_folder_path="/Inbox")
    assert m.sent_at is None
    assert m.is_read is True
    assert m.has_attachments is True
    assert m.importance == "high"
    assert m.categories == ["Followup"]
    assert m.inference_classification == "other"
    assert m.body == Body(content_type="html", content="<p>hi</p>")


# ---- MailboxSettings + AutomaticRepliesSetting -----------------------------

def test_auto_reply_from_graph_json_disabled():
    raw = {
        "status": "disabled",
        "externalAudience": "none",
        "scheduledStartDateTime": {"dateTime": "2026-04-24T00:00:00.0000000", "timeZone": "UTC"},
        "scheduledEndDateTime": {"dateTime": "2026-04-24T23:59:59.0000000", "timeZone": "UTC"},
        "internalReplyMessage": "",
        "externalReplyMessage": "",
    }
    ar = AutomaticRepliesSetting.from_graph_json(raw)
    assert ar.status == "disabled"
    assert ar.external_audience == "none"
    assert ar.scheduled_start == datetime(2026, 4, 24, 0, 0, tzinfo=timezone.utc)


def test_mailbox_settings_from_graph_json_minimal():
    raw = {
        "timeZone": "Europe/Istanbul",
        "language": {"locale": "en-US", "displayName": "English (United States)"},
        "workingHours": {
            "daysOfWeek": ["monday", "tuesday", "wednesday", "thursday", "friday"],
            "startTime": "09:00:00.0000000",
            "endTime": "17:00:00.0000000",
            "timeZone": {"name": "Europe/Istanbul"},
        },
        "automaticRepliesSetting": {
            "status": "disabled",
            "externalAudience": "none",
            "scheduledStartDateTime": {"dateTime": "2026-04-24T00:00:00.0000000", "timeZone": "UTC"},
            "scheduledEndDateTime": {"dateTime": "2026-04-24T23:59:59.0000000", "timeZone": "UTC"},
            "internalReplyMessage": "",
            "externalReplyMessage": "",
        },
        "delegateMeetingMessageDeliveryOptions": "sendToDelegateAndInformationToPrincipal",
        "dateFormat": "yyyy-MM-dd",
        "timeFormat": "HH:mm",
    }
    s = MailboxSettings.from_graph_json(raw)
    assert s.timezone == "Europe/Istanbul"
    assert s.language == LocaleInfo(locale="en-US", display_name="English (United States)")
    assert s.working_hours.days == ["monday", "tuesday", "wednesday", "thursday", "friday"]
    assert s.working_hours.start_time == "09:00:00"
    assert s.working_hours.end_time == "17:00:00"
    assert s.auto_reply.status == "disabled"
    assert s.delegate_meeting_message_delivery == "sendToDelegateAndInformationToPrincipal"
    assert s.date_format == "yyyy-MM-dd"
    assert s.time_format == "HH:mm"
```

Run: `uv run pytest tests/test_mail_models.py -q`  → all FAIL (module missing).

- [ ] **Step 2: Implement `src/m365ctl/mail/models.py`.**

Spec §8 lists the target shape. Add `from_graph_json` parsers for each:

```python
"""Dataclass mirrors of Graph mail entities, plus ``from_graph_json`` parsers.

Every dataclass is ``frozen=True``. Parsers are defensive: missing optional
fields produce zero-values, but missing REQUIRED fields raise ``KeyError``
(catch at the call site if you need graceful degradation).

Spec reference: §8 (Data model).
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


FlagStatus = Literal["notFlagged", "flagged", "complete"]
InferenceClassification = Literal["focused", "other"]
Importance = Literal["low", "normal", "high"]
BodyContentType = Literal["text", "html"]
ExternalAudience = Literal["none", "contactsOnly", "all"]
AutoReplyStatus = Literal["disabled", "alwaysEnabled", "scheduled"]
AttachmentKind = Literal["file", "item", "reference"]


def _parse_graph_datetime(raw: dict | str | None) -> datetime | None:
    """Parse Graph's ``dateTime`` / timeZone pair or ISO-8601 string.

    Graph uses two shapes interchangeably:
    - ``"2026-04-24T10:00:00Z"`` (ISO-8601 with Z)
    - ``{"dateTime": "2026-04-24T10:00:00.0000000", "timeZone": "UTC"}``

    Returns ``None`` for None-or-empty inputs.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        if not raw:
            return None
        # ISO-8601; Z or explicit offset.
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    # dict form
    dt_str = raw.get("dateTime")
    tz_str = raw.get("timeZone") or "UTC"
    if not dt_str:
        return None
    # Graph's 7-digit microseconds exceed Python's 6-digit limit — trim.
    if "." in dt_str:
        head, frac = dt_str.split(".", 1)
        frac = frac[:6]  # microsecond precision
        dt_str = f"{head}.{frac}"
    dt = datetime.fromisoformat(dt_str)
    # Attach tz if naive.
    if dt.tzinfo is None and tz_str.upper() == "UTC":
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass(frozen=True)
class EmailAddress:
    name: str
    address: str

    @classmethod
    def from_graph_json(cls, raw: dict | None) -> "EmailAddress":
        if raw is None:
            return cls(name="", address="")
        # Graph wraps most addresses in {"emailAddress": {...}}; sometimes flat.
        inner = raw.get("emailAddress", raw)
        return cls(
            name=inner.get("name", "") or "",
            address=inner.get("address", "") or "",
        )


@dataclass(frozen=True)
class Body:
    content_type: BodyContentType
    content: str

    @classmethod
    def from_graph_json(cls, raw: dict) -> "Body":
        return cls(content_type=raw["contentType"], content=raw.get("content", ""))


@dataclass(frozen=True)
class Flag:
    status: FlagStatus
    start_at: datetime | None = None
    due_at: datetime | None = None
    completed_at: datetime | None = None

    @classmethod
    def from_graph_json(cls, raw: dict) -> "Flag":
        return cls(
            status=raw.get("flagStatus", "notFlagged"),
            start_at=_parse_graph_datetime(raw.get("startDateTime")),
            due_at=_parse_graph_datetime(raw.get("dueDateTime")),
            completed_at=_parse_graph_datetime(raw.get("completedDateTime")),
        )


@dataclass(frozen=True)
class Folder:
    id: str
    mailbox_upn: str
    display_name: str
    parent_id: str | None
    path: str
    total_items: int
    unread_items: int
    child_folder_count: int
    well_known_name: str | None

    @classmethod
    def from_graph_json(cls, raw: dict, *, mailbox_upn: str, path: str) -> "Folder":
        return cls(
            id=raw["id"],
            mailbox_upn=mailbox_upn,
            display_name=raw.get("displayName", ""),
            parent_id=raw.get("parentFolderId"),
            path=path,
            total_items=raw.get("totalItemCount", 0),
            unread_items=raw.get("unreadItemCount", 0),
            child_folder_count=raw.get("childFolderCount", 0),
            well_known_name=raw.get("wellKnownName"),
        )


@dataclass(frozen=True)
class Category:
    id: str
    display_name: str
    color: str

    @classmethod
    def from_graph_json(cls, raw: dict) -> "Category":
        return cls(
            id=raw["id"],
            display_name=raw.get("displayName", ""),
            color=raw.get("color", "preset0"),
        )


@dataclass(frozen=True)
class Rule:
    id: str
    display_name: str
    sequence: int
    is_enabled: bool
    has_error: bool
    is_read_only: bool
    conditions: dict
    actions: dict
    exceptions: dict

    @classmethod
    def from_graph_json(cls, raw: dict) -> "Rule":
        return cls(
            id=raw["id"],
            display_name=raw.get("displayName", ""),
            sequence=raw.get("sequence", 0),
            is_enabled=raw.get("isEnabled", False),
            has_error=raw.get("hasError", False),
            is_read_only=raw.get("isReadOnly", False),
            conditions=raw.get("conditions", {}) or {},
            actions=raw.get("actions", {}) or {},
            exceptions=raw.get("exceptions", {}) or {},
        )


_ATTACHMENT_KIND_BY_ODATA_TYPE = {
    "#microsoft.graph.fileAttachment": "file",
    "#microsoft.graph.itemAttachment": "item",
    "#microsoft.graph.referenceAttachment": "reference",
}


@dataclass(frozen=True)
class Attachment:
    id: str
    message_id: str
    kind: AttachmentKind
    name: str
    content_type: str
    size: int
    is_inline: bool
    content_id: str | None

    @classmethod
    def from_graph_json(cls, raw: dict, *, message_id: str) -> "Attachment":
        odata_type = raw.get("@odata.type", "")
        kind = _ATTACHMENT_KIND_BY_ODATA_TYPE.get(odata_type, "file")
        return cls(
            id=raw["id"],
            message_id=message_id,
            kind=kind,  # type: ignore[arg-type]
            name=raw.get("name", ""),
            content_type=raw.get("contentType", ""),
            size=raw.get("size", 0),
            is_inline=raw.get("isInline", False),
            content_id=raw.get("contentId"),
        )


@dataclass(frozen=True)
class LocaleInfo:
    locale: str
    display_name: str

    @classmethod
    def from_graph_json(cls, raw: dict) -> "LocaleInfo":
        return cls(
            locale=raw.get("locale", ""),
            display_name=raw.get("displayName", ""),
        )


@dataclass(frozen=True)
class WorkingHours:
    days: list[str]
    start_time: str   # "HH:MM:SS"
    end_time: str
    time_zone: str

    @classmethod
    def from_graph_json(cls, raw: dict) -> "WorkingHours":
        tz_block = raw.get("timeZone", {}) or {}
        # Normalize Graph's 7-digit fractional seconds to a plain HH:MM:SS.
        def _trim(t: str) -> str:
            return t.split(".", 1)[0] if t else ""
        return cls(
            days=list(raw.get("daysOfWeek", [])),
            start_time=_trim(raw.get("startTime", "")),
            end_time=_trim(raw.get("endTime", "")),
            time_zone=tz_block.get("name", ""),
        )


@dataclass(frozen=True)
class AutomaticRepliesSetting:
    status: AutoReplyStatus
    external_audience: ExternalAudience
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    internal_reply_message: str
    external_reply_message: str

    @classmethod
    def from_graph_json(cls, raw: dict) -> "AutomaticRepliesSetting":
        return cls(
            status=raw.get("status", "disabled"),
            external_audience=raw.get("externalAudience", "none"),
            scheduled_start=_parse_graph_datetime(raw.get("scheduledStartDateTime")),
            scheduled_end=_parse_graph_datetime(raw.get("scheduledEndDateTime")),
            internal_reply_message=raw.get("internalReplyMessage", ""),
            external_reply_message=raw.get("externalReplyMessage", ""),
        )


@dataclass(frozen=True)
class MailboxSettings:
    timezone: str
    language: LocaleInfo
    working_hours: WorkingHours
    auto_reply: AutomaticRepliesSetting
    delegate_meeting_message_delivery: str
    date_format: str
    time_format: str

    @classmethod
    def from_graph_json(cls, raw: dict) -> "MailboxSettings":
        return cls(
            timezone=raw.get("timeZone", ""),
            language=LocaleInfo.from_graph_json(raw.get("language", {}) or {}),
            working_hours=WorkingHours.from_graph_json(raw.get("workingHours", {}) or {}),
            auto_reply=AutomaticRepliesSetting.from_graph_json(
                raw.get("automaticRepliesSetting", {}) or {}
            ),
            delegate_meeting_message_delivery=raw.get("delegateMeetingMessageDeliveryOptions", ""),
            date_format=raw.get("dateFormat", ""),
            time_format=raw.get("timeFormat", ""),
        )


@dataclass(frozen=True)
class Message:
    id: str
    mailbox_upn: str
    internet_message_id: str
    conversation_id: str
    conversation_index: bytes
    parent_folder_id: str
    parent_folder_path: str
    subject: str
    sender: EmailAddress
    from_addr: EmailAddress
    to: list[EmailAddress]
    cc: list[EmailAddress]
    bcc: list[EmailAddress]
    reply_to: list[EmailAddress]
    received_at: datetime
    sent_at: datetime | None
    is_read: bool
    is_draft: bool
    has_attachments: bool
    importance: Importance
    flag: Flag
    categories: list[str]
    inference_classification: InferenceClassification
    body_preview: str
    body: Body | None
    web_link: str
    change_key: str

    @classmethod
    def from_graph_json(
        cls,
        raw: dict,
        *,
        mailbox_upn: str,
        parent_folder_path: str,
    ) -> "Message":
        def _addrs(key: str) -> list[EmailAddress]:
            return [EmailAddress.from_graph_json(x) for x in raw.get(key, []) or []]

        conv_idx_b64 = raw.get("conversationIndex", "") or ""
        conv_idx = base64.b64decode(conv_idx_b64) if conv_idx_b64 else b""

        received = _parse_graph_datetime(raw.get("receivedDateTime"))
        assert received is not None, "receivedDateTime missing from Graph message payload"

        body_raw = raw.get("body")
        body = Body.from_graph_json(body_raw) if body_raw else None

        return cls(
            id=raw["id"],
            mailbox_upn=mailbox_upn,
            internet_message_id=raw.get("internetMessageId", ""),
            conversation_id=raw.get("conversationId", ""),
            conversation_index=conv_idx,
            parent_folder_id=raw.get("parentFolderId", ""),
            parent_folder_path=parent_folder_path,
            subject=raw.get("subject", ""),
            sender=EmailAddress.from_graph_json(raw.get("sender")),
            from_addr=EmailAddress.from_graph_json(raw.get("from")),
            to=_addrs("toRecipients"),
            cc=_addrs("ccRecipients"),
            bcc=_addrs("bccRecipients"),
            reply_to=_addrs("replyTo"),
            received_at=received,
            sent_at=_parse_graph_datetime(raw.get("sentDateTime")),
            is_read=raw.get("isRead", False),
            is_draft=raw.get("isDraft", False),
            has_attachments=raw.get("hasAttachments", False),
            importance=raw.get("importance", "normal"),
            flag=Flag.from_graph_json(raw.get("flag", {}) or {}),
            categories=list(raw.get("categories", []) or []),
            inference_classification=raw.get("inferenceClassification", "focused"),
            body_preview=raw.get("bodyPreview", ""),
            body=body,
            web_link=raw.get("webLink", ""),
            change_key=raw.get("changeKey", ""),
        )
```

- [ ] **Step 3: Run the model tests + full suite.**

```bash
uv run pytest tests/test_mail_models.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
```
Expected: model tests all pass; suite count = 254 + 17 new = 271.

- [ ] **Step 4: Commit.**

```bash
git add src/m365ctl/mail/models.py tests/test_mail_models.py
git commit -m "feat(mail): models with from_graph_json parsers for Message, Folder, Category, Rule, Attachment, MailboxSettings"
```

---

## Group 2: Safety — assert_mailbox_allowed + is_folder_denied

### Task 4: Add mailbox + folder safety primitives

**Files:**
- Modify: `src/m365ctl/common/safety.py`
- Create: `tests/test_mail_safety.py`

- [ ] **Step 1: Failing tests.**

Write `tests/test_mail_safety.py`:
```python
"""Tests for m365ctl.common.safety — mailbox + folder gates."""
from __future__ import annotations

from pathlib import Path

import pytest

from m365ctl.common.config import (
    CatalogConfig,
    Config,
    LoggingConfig,
    MailConfig,
    ScopeConfig,
)
from m365ctl.common.safety import (
    HARDCODED_DENY_FOLDERS,
    ScopeViolation,
    assert_mailbox_allowed,
    is_folder_denied,
)


def _cfg(allow_mailboxes: list[str], deny_folders: list[str] | None = None) -> Config:
    return Config(
        tenant_id="00000000-0000-0000-0000-000000000000",
        client_id="11111111-1111-1111-1111-111111111111",
        cert_path=Path("/tmp/x.key"),
        cert_public=Path("/tmp/x.cer"),
        default_auth="delegated",
        scope=ScopeConfig(
            allow_drives=["me"],
            allow_mailboxes=allow_mailboxes,
            deny_folders=deny_folders or [],
        ),
        catalog=CatalogConfig(path=Path("cache/catalog.duckdb"), refresh_on_start=False),
        mail=MailConfig(catalog_path=Path("cache/mail.duckdb")),
        logging=LoggingConfig(ops_dir=Path("logs/ops")),
    )


# ---- assert_mailbox_allowed ------------------------------------------------

def test_me_allowed_when_me_in_list():
    cfg = _cfg(allow_mailboxes=["me"])
    assert_mailbox_allowed("me", cfg, auth_mode="delegated", unsafe_scope=False)


def test_me_rejected_when_not_in_list():
    cfg = _cfg(allow_mailboxes=["upn:boss@example.com"])
    with pytest.raises(ScopeViolation):
        assert_mailbox_allowed("me", cfg, auth_mode="delegated", unsafe_scope=False)


def test_upn_matches_upn_in_list():
    cfg = _cfg(allow_mailboxes=["upn:alice@example.com"])
    assert_mailbox_allowed("upn:alice@example.com", cfg, auth_mode="app-only", unsafe_scope=False)


def test_upn_case_insensitive():
    # Email addresses are case-insensitive per RFC; allow Alice@ to match alice@.
    cfg = _cfg(allow_mailboxes=["upn:alice@example.com"])
    assert_mailbox_allowed("upn:ALICE@example.com", cfg, auth_mode="app-only", unsafe_scope=False)


def test_shared_matches_shared_in_list():
    cfg = _cfg(allow_mailboxes=["shared:ops@example.com"])
    assert_mailbox_allowed("shared:ops@example.com", cfg, auth_mode="delegated", unsafe_scope=False)


def test_shared_does_not_match_upn_entry():
    # shared:X and upn:X are distinct scope entries.
    cfg = _cfg(allow_mailboxes=["upn:ops@example.com"])
    with pytest.raises(ScopeViolation):
        assert_mailbox_allowed("shared:ops@example.com", cfg, auth_mode="delegated", unsafe_scope=False)


def test_wildcard_star_requires_app_only():
    cfg = _cfg(allow_mailboxes=["*"])
    # Delegated + * is nonsensical: delegated is implicitly just "me".
    with pytest.raises(ScopeViolation) as ei:
        assert_mailbox_allowed("upn:random@example.com", cfg, auth_mode="delegated", unsafe_scope=False)
    assert "app-only" in str(ei.value).lower()


def test_wildcard_star_allows_app_only():
    cfg = _cfg(allow_mailboxes=["*"])
    assert_mailbox_allowed("upn:random@example.com", cfg, auth_mode="app-only", unsafe_scope=False)


def test_unsafe_scope_still_rejects_without_tty():
    # Mailbox not in list; --unsafe-scope would allow, but assert_mailbox_allowed
    # falls through to the same /dev/tty check that scope.assert_scope_allowed uses.
    # In non-TTY test env, confirm returns False → ScopeViolation.
    cfg = _cfg(allow_mailboxes=["me"])
    with pytest.raises(ScopeViolation):
        assert_mailbox_allowed("upn:other@example.com", cfg, auth_mode="app-only", unsafe_scope=True)


# ---- is_folder_denied ------------------------------------------------------

@pytest.mark.parametrize("path", [
    "Recoverable Items",
    "Recoverable Items/Deletions",
    "Purges",
    "Purges/a/b/c",
    "Audits",
    "Calendar",
    "Calendar/Work",
    "Contacts",
    "Tasks",
    "Notes",
])
def test_is_folder_denied_hardcoded_hits(path):
    cfg = _cfg(allow_mailboxes=["me"])
    assert is_folder_denied(path, cfg), f"{path!r} should be denied"


@pytest.mark.parametrize("path", [
    "Inbox",
    "Inbox/Triage",
    "Sent Items",
    "Drafts",
    "Archive/2026",
    "",
])
def test_is_folder_denied_allows_normal_paths(path):
    cfg = _cfg(allow_mailboxes=["me"])
    assert not is_folder_denied(path, cfg), f"{path!r} should be allowed"


def test_is_folder_denied_user_config_pattern():
    cfg = _cfg(allow_mailboxes=["me"], deny_folders=["Archive/Legal/*"])
    assert is_folder_denied("Archive/Legal/2026", cfg)
    assert not is_folder_denied("Archive/2026", cfg)


def test_hardcoded_deny_folders_list_is_read_only():
    # Any caller mutating this constant would bleed into subsequent tests.
    # Confirm immutability by type (tuple/frozenset) at module import time.
    assert isinstance(HARDCODED_DENY_FOLDERS, frozenset)
```

Run: `uv run pytest tests/test_mail_safety.py -q`  → all FAIL (imports missing).

- [ ] **Step 2: Implement `assert_mailbox_allowed` + `is_folder_denied` in `src/m365ctl/common/safety.py`.**

Append to the end of `src/m365ctl/common/safety.py` (keep the existing content intact):
```python
# ---- Mailbox + folder gates (Phase 1) --------------------------------------

# Hard-coded folder patterns that are ALWAYS denied (spec §11.2). These are
# non-negotiable compliance/out-of-scope buckets; user config cannot override.
HARDCODED_DENY_FOLDERS: frozenset[str] = frozenset({
    "Recoverable Items",
    "Recoverable Items/*",
    "Purges",
    "Purges/*",
    "Audits",
    "Audits/*",
    "Calendar",
    "Calendar/*",
    "Contacts",
    "Contacts/*",
    "Tasks",
    "Tasks/*",
    "Notes",
    "Notes/*",
})


def _mailbox_spec_matches(actual_spec: str, allowed_entry: str) -> bool:
    """Return True iff ``actual_spec`` satisfies ``allowed_entry``.

    Entries are compared case-insensitively on the address portion (email
    addresses are case-insensitive per RFC 5321). Exact (kind, address)
    tuple match.
    """
    # Normalize both — split off kind if present, lowercase the address.
    def _split(s: str) -> tuple[str, str]:
        for prefix, kind in (("upn:", "upn"), ("shared:", "shared")):
            if s.startswith(prefix):
                return (kind, s[len(prefix):].strip().lower())
        # Bare keywords: "me", "*".
        return (s.strip().lower(), "")

    a_kind, a_addr = _split(actual_spec)
    e_kind, e_addr = _split(allowed_entry)
    return a_kind == e_kind and a_addr == e_addr


def assert_mailbox_allowed(
    mailbox_spec: str,
    cfg: Config,
    *,
    auth_mode: str,
    unsafe_scope: bool,
) -> None:
    """Raise ``ScopeViolation`` unless ``mailbox_spec`` is in ``allow_mailboxes``.

    Matching semantics (spec §11.1):
    - ``"me"`` matches ``"me"``.
    - ``"upn:alice@example.com"`` matches ``"upn:alice@example.com"``  (case-insensitive address).
    - ``"shared:team@example.com"`` matches ``"shared:team@example.com"`` — NOT ``"upn:team@example.com"``.
    - ``"*"`` in ``allow_mailboxes`` matches any spec, but ONLY under app-only auth.
    - ``--unsafe-scope`` falls through to ``/dev/tty`` confirm (same behavior as drive scope).

    Callers must pass ``auth_mode`` because the ``"*"`` entry is app-only only.
    """
    allow = cfg.scope.allow_mailboxes

    # Wildcard fast path: only app-only is allowed to use "*".
    if "*" in allow:
        if auth_mode != "app-only":
            raise ScopeViolation(
                "mailbox scope '*' is app-only only; use a specific allow_mailboxes entry for delegated flows"
            )
        return

    for entry in allow:
        if _mailbox_spec_matches(mailbox_spec, entry):
            return

    if not unsafe_scope:
        raise ScopeViolation(
            f"mailbox {mailbox_spec!r} not in scope.allow_mailboxes; "
            f"pass --unsafe-scope to override (requires TTY confirm)"
        )

    prompt = (
        f"UNSAFE SCOPE: mailbox {mailbox_spec!r} is outside allow_mailboxes.\n"
        f"Proceed anyway? [y/N]: "
    )
    if not _confirm_via_tty(prompt):
        raise ScopeViolation(
            f"user declined /dev/tty confirm for unsafe-scope mailbox {mailbox_spec!r}"
        )


def is_folder_denied(folder_path: str, cfg: Config) -> bool:
    """Return True if ``folder_path`` matches the hard-coded deny list OR a
    user-configured pattern in ``scope.deny_folders``.

    Match semantics: ``fnmatch`` glob. Paths are matched BOTH against the
    pattern and (for patterns ending in ``/*``) against the bare parent.
    """
    for pat in HARDCODED_DENY_FOLDERS | frozenset(cfg.scope.deny_folders):
        if fnmatch.fnmatch(folder_path, pat):
            return True
        # A pattern like "Recoverable Items/*" should also match the parent itself.
        if pat.endswith("/*") and folder_path == pat[:-2]:
            return True
    return False
```

- [ ] **Step 3: Run the new tests + full suite.**

```bash
uv run pytest tests/test_mail_safety.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
```
Expected: 271 + 22 new = 293 passed.

- [ ] **Step 4: Commit.**

```bash
git add src/m365ctl/common/safety.py tests/test_mail_safety.py
git commit -m "feat(safety): assert_mailbox_allowed + is_folder_denied with hardcoded compliance deny list"
```

---

## Group 3: Messages readers

### Task 5: `mail/messages.py` — list_messages, get_message, search_messages_graph, get_thread

**Files:**
- Create: `src/m365ctl/mail/messages.py`
- Create: `tests/test_mail_messages.py`

- [ ] **Step 1: Failing tests (mocked Graph client).**

Create `tests/test_mail_messages.py`:
```python
"""Tests for m365ctl.mail.messages — readers over a mocked Graph client."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from m365ctl.mail.messages import (
    MessageListFilters,
    get_message,
    get_thread,
    list_messages,
    search_messages_graph,
)
from m365ctl.mail.models import Message


def _graph_with_single_page(pages: list[dict]) -> MagicMock:
    """Stub GraphClient.get_paginated to yield ``pages``."""
    graph = MagicMock()
    # get_paginated returns iterator of (items, delta_link). Convert input pages.
    graph.get_paginated.return_value = iter([(p.get("value", []), None) for p in pages])
    return graph


def _msg_raw(msg_id: str = "m1", folder_id: str = "folder-1") -> dict:
    return {
        "id": msg_id,
        "internetMessageId": f"<{msg_id}@example.com>",
        "conversationId": f"conv-{msg_id}",
        "conversationIndex": "AQ==",
        "parentFolderId": folder_id,
        "subject": f"Subj {msg_id}",
        "sender": {"emailAddress": {"name": "A", "address": "a@example.com"}},
        "from": {"emailAddress": {"name": "A", "address": "a@example.com"}},
        "toRecipients": [],
        "ccRecipients": [],
        "bccRecipients": [],
        "replyTo": [],
        "receivedDateTime": "2026-04-24T10:00:00Z",
        "sentDateTime": "2026-04-24T09:59:55Z",
        "isRead": False,
        "isDraft": False,
        "hasAttachments": False,
        "importance": "normal",
        "flag": {"flagStatus": "notFlagged"},
        "categories": [],
        "inferenceClassification": "focused",
        "bodyPreview": "...",
        "webLink": "https://x",
        "changeKey": "ck",
    }


def test_list_messages_basic_inbox():
    graph = _graph_with_single_page([{"value": [_msg_raw("m1"), _msg_raw("m2")]}])
    out = list(list_messages(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        folder_id="AAMkAD..inbox",
        parent_folder_path="/Inbox",
    ))
    assert len(out) == 2
    assert all(isinstance(m, Message) for m in out)
    assert [m.id for m in out] == ["m1", "m2"]
    # Confirm URL routing.
    call_args = graph.get_paginated.call_args
    assert call_args.args[0] == "/me/mailFolders/AAMkAD..inbox/messages"


def test_list_messages_app_only_routes_via_users_upn():
    graph = _graph_with_single_page([{"value": [_msg_raw()]}])
    list(list_messages(
        graph,
        mailbox_spec="upn:bob@example.com",
        auth_mode="app-only",
        folder_id="AAMkAD..inbox",
        parent_folder_path="/Inbox",
    ))
    url = graph.get_paginated.call_args.args[0]
    assert url == "/users/bob@example.com/mailFolders/AAMkAD..inbox/messages"


def test_list_messages_filters_odata():
    graph = _graph_with_single_page([{"value": []}])
    filters = MessageListFilters(
        unread=True,
        from_address="alice@example.com",
        subject_contains="meeting",
        since="2026-04-20T00:00:00Z",
        until="2026-04-24T00:00:00Z",
        has_attachments=True,
        importance="high",
        focus="focused",
        category="Followup",
    )
    list(list_messages(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        folder_id="inbox",
        parent_folder_path="/Inbox",
        filters=filters,
        limit=10,
    ))
    kwargs = graph.get_paginated.call_args.kwargs
    params = kwargs["params"]
    # Build the $filter clause expected.
    f = params["$filter"]
    assert "isRead eq false" in f
    assert "from/emailAddress/address eq 'alice@example.com'" in f
    assert "contains(subject, 'meeting')" in f
    assert "receivedDateTime ge 2026-04-20T00:00:00Z" in f
    assert "receivedDateTime le 2026-04-24T00:00:00Z" in f
    assert "hasAttachments eq true" in f
    assert "importance eq 'high'" in f
    assert "inferenceClassification eq 'focused'" in f
    assert "categories/any(c:c eq 'Followup')" in f
    assert params["$top"] == 10
    assert params["$orderby"] == "receivedDateTime desc"


def test_list_messages_limit_stops_iteration():
    graph = _graph_with_single_page([
        {"value": [_msg_raw(f"m{i}") for i in range(50)]},
    ])
    out = list(list_messages(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        folder_id="inbox",
        parent_folder_path="/Inbox",
        limit=5,
    ))
    assert len(out) == 5


def test_get_message_with_body_expands_attachments():
    graph = MagicMock()
    graph.get.return_value = _msg_raw("m1")
    m = get_message(graph, mailbox_spec="me", auth_mode="delegated", message_id="m1", with_attachments=True)
    assert m.id == "m1"
    url = graph.get.call_args.args[0]
    assert url == "/me/messages/m1"
    params = graph.get.call_args.kwargs.get("params", {})
    assert params.get("$expand") == "attachments"


def test_search_messages_graph_posts_query():
    graph = MagicMock()
    graph.post.return_value = {
        "value": [{
            "hitsContainers": [{
                "hits": [
                    {"resource": _msg_raw("hit1")},
                    {"resource": _msg_raw("hit2")},
                ]
            }]
        }]
    }
    out = list(search_messages_graph(graph, query="invoice", limit=25))
    assert len(out) == 2
    assert out[0].id == "hit1"
    assert graph.post.call_args.args[0] == "/search/query"
    payload = graph.post.call_args.kwargs["json"]
    assert payload["requests"][0]["entityTypes"] == ["message"]
    assert payload["requests"][0]["query"]["queryString"] == "invoice"
    assert payload["requests"][0]["size"] == 25


def test_get_thread_walks_conversation_id():
    graph = _graph_with_single_page([{
        "value": [_msg_raw("m1"), _msg_raw("m2"), _msg_raw("m3")],
    }])
    out = list(get_thread(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        conversation_id="conv-m1",
        parent_folder_path="/Inbox",
    ))
    assert [m.id for m in out] == ["m1", "m2", "m3"]
    # get_thread queries /me/messages with $filter=conversationId eq '…'
    url = graph.get_paginated.call_args.args[0]
    assert url == "/me/messages"
    params = graph.get_paginated.call_args.kwargs["params"]
    assert "conversationId eq 'conv-m1'" in params["$filter"]
    assert params["$orderby"] == "receivedDateTime asc"
```

Run: `uv run pytest tests/test_mail_messages.py -q`  → all FAIL (module missing).

- [ ] **Step 2: Implement `src/m365ctl/mail/messages.py`.**

```python
"""Read-only message operations over Microsoft Graph.

All functions take a ``GraphClient`` and return ``Message`` dataclasses
(or iterators thereof). Pagination is handled via ``GraphClient.get_paginated``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.models import Message


@dataclass(frozen=True)
class MessageListFilters:
    """OData $filter inputs for ``list_messages``.

    Each field maps to a single ``$filter`` clause; clauses are ANDed.
    Leave any field at its default to omit the clause.
    """
    unread: bool | None = None                  # True → isRead eq false
    from_address: str | None = None             # exact match on from/emailAddress/address
    subject_contains: str | None = None         # contains(subject, '…')
    since: str | None = None                    # receivedDateTime ge <iso>
    until: str | None = None                    # receivedDateTime le <iso>
    has_attachments: bool | None = None
    importance: str | None = None               # 'low' | 'normal' | 'high'
    focus: str | None = None                    # 'focused' | 'other'
    category: str | None = None                 # single category (any(c:c eq 'X'))


def _build_filter_expr(f: MessageListFilters) -> str:
    clauses: list[str] = []
    if f.unread is True:
        clauses.append("isRead eq false")
    elif f.unread is False:
        clauses.append("isRead eq true")
    if f.from_address:
        clauses.append(f"from/emailAddress/address eq '{f.from_address}'")
    if f.subject_contains:
        # Single-quote escape: Graph OData doubles single quotes.
        esc = f.subject_contains.replace("'", "''")
        clauses.append(f"contains(subject, '{esc}')")
    if f.since:
        clauses.append(f"receivedDateTime ge {f.since}")
    if f.until:
        clauses.append(f"receivedDateTime le {f.until}")
    if f.has_attachments is True:
        clauses.append("hasAttachments eq true")
    elif f.has_attachments is False:
        clauses.append("hasAttachments eq false")
    if f.importance:
        clauses.append(f"importance eq '{f.importance}'")
    if f.focus:
        clauses.append(f"inferenceClassification eq '{f.focus}'")
    if f.category:
        esc = f.category.replace("'", "''")
        clauses.append(f"categories/any(c:c eq '{esc}')")
    return " and ".join(clauses)


def list_messages(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    folder_id: str,
    parent_folder_path: str,
    filters: MessageListFilters | None = None,
    limit: int | None = None,
    page_size: int = 50,
) -> Iterator[Message]:
    """Yield messages from ``folder_id``, optionally filtered.

    ``parent_folder_path`` is threaded through to the Message dataclass so
    catalog writes and display lines can show human-readable folder paths.
    """
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    path = f"{ub}/mailFolders/{folder_id}/messages"

    filters = filters or MessageListFilters()
    filter_expr = _build_filter_expr(filters)

    params: dict = {
        "$orderby": "receivedDateTime desc",
        "$top": limit if limit is not None else page_size,
    }
    if filter_expr:
        params["$filter"] = filter_expr

    count = 0
    # mailbox_upn is the address-or-"me" string; callers passing upn:<addr>
    # want the UPN attached to each Message for catalog attribution.
    mailbox_upn = _derive_mailbox_upn(mailbox_spec)
    for items, _ in graph.get_paginated(path, params=params):
        for raw in items:
            yield Message.from_graph_json(
                raw, mailbox_upn=mailbox_upn, parent_folder_path=parent_folder_path,
            )
            count += 1
            if limit is not None and count >= limit:
                return


def _derive_mailbox_upn(mailbox_spec: str) -> str:
    """Return the address-or-keyword for Message.mailbox_upn."""
    if mailbox_spec == "me":
        return "me"
    if mailbox_spec.startswith("upn:"):
        return mailbox_spec[len("upn:"):]
    if mailbox_spec.startswith("shared:"):
        return mailbox_spec[len("shared:"):]
    return mailbox_spec


def get_message(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    message_id: str,
    with_attachments: bool = False,
    parent_folder_path: str = "",
) -> Message:
    """Fetch a single message by id. ``with_attachments=True`` $expands the attachments."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    path = f"{ub}/messages/{message_id}"
    params: dict = {}
    if with_attachments:
        params["$expand"] = "attachments"
    raw = graph.get(path, params=params or None)
    mailbox_upn = _derive_mailbox_upn(mailbox_spec)
    return Message.from_graph_json(raw, mailbox_upn=mailbox_upn, parent_folder_path=parent_folder_path)


def search_messages_graph(
    graph: GraphClient,
    *,
    query: str,
    limit: int = 25,
) -> Iterator[Message]:
    """Server-side /search/query across all mail folders the caller can see.

    Returns Messages ordered by Graph's relevance scoring. The caller's
    auth token determines which mailboxes are searched (delegated → /me,
    app-only → all of tenant).
    """
    payload = {
        "requests": [
            {
                "entityTypes": ["message"],
                "query": {"queryString": query},
                "from": 0,
                "size": limit,
            }
        ]
    }
    resp = graph.post("/search/query", json=payload)
    for response in resp.get("value", []):
        for container in response.get("hitsContainers", []):
            for hit in container.get("hits", []):
                raw = hit.get("resource")
                if not raw:
                    continue
                # /search/query doesn't embed mailbox_upn; caller context
                # does. The raw hit has the top-level Message shape.
                yield Message.from_graph_json(
                    raw,
                    mailbox_upn=_derive_mailbox_upn("me"),  # relevance scoring is caller-scoped
                    parent_folder_path="",
                )


def get_thread(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    conversation_id: str,
    parent_folder_path: str = "",
) -> Iterator[Message]:
    """Walk all messages in ``conversation_id`` chronologically (oldest first)."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    path = f"{ub}/messages"
    params = {
        "$filter": f"conversationId eq '{conversation_id}'",
        "$orderby": "receivedDateTime asc",
        "$top": 200,
    }
    mailbox_upn = _derive_mailbox_upn(mailbox_spec)
    for items, _ in graph.get_paginated(path, params=params):
        for raw in items:
            yield Message.from_graph_json(
                raw, mailbox_upn=mailbox_upn, parent_folder_path=parent_folder_path,
            )
```

- [ ] **Step 3: Run tests.**

```bash
uv run pytest tests/test_mail_messages.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
```
Expected: all new tests pass; total ≈ 300.

- [ ] **Step 4: Commit.**

```bash
git add src/m365ctl/mail/messages.py tests/test_mail_messages.py
git commit -m "feat(mail): messages readers — list/get/search_graph/thread with OData filter builder"
```

---

## Group 4: Folders reader + path resolver

### Task 6: `mail/folders.py` — list_folders, resolve_folder_path, get_folder

**Files:**
- Create: `src/m365ctl/mail/folders.py`
- Create: `tests/test_mail_folders.py`

- [ ] **Step 1: Failing tests.**

Write `tests/test_mail_folders.py`:
```python
"""Tests for m365ctl.mail.folders — recursive folder walk + path resolution."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from m365ctl.mail.folders import (
    FolderNotFound,
    get_folder,
    list_folders,
    resolve_folder_path,
)
from m365ctl.mail.models import Folder


def _folder_raw(fid: str, name: str, parent: str | None = None, well_known: str | None = None,
                child_count: int = 0, total: int = 0, unread: int = 0) -> dict:
    return {
        "id": fid,
        "displayName": name,
        "parentFolderId": parent,
        "childFolderCount": child_count,
        "totalItemCount": total,
        "unreadItemCount": unread,
        "wellKnownName": well_known,
    }


def _graph_flat_tree(root_children: list[dict], sub_map: dict[str, list[dict]] | None = None) -> MagicMock:
    """Make GraphClient.get_paginated return root children, then expand sub-folders per id."""
    graph = MagicMock()
    sub_map = sub_map or {}

    def _paginated(path, params=None):
        # Top-level: /me/mailFolders
        if path.endswith("/mailFolders"):
            return iter([(root_children, None)])
        # Sub: /me/mailFolders/<id>/childFolders
        for fid, kids in sub_map.items():
            if path.endswith(f"/mailFolders/{fid}/childFolders"):
                return iter([(kids, None)])
        return iter([([], None)])

    graph.get_paginated.side_effect = _paginated
    return graph


def test_list_folders_flat_root():
    graph = _graph_flat_tree([
        _folder_raw("f1", "Inbox", well_known="inbox", child_count=0),
        _folder_raw("f2", "Sent Items", well_known="sentitems"),
    ])
    out = list(list_folders(graph, mailbox_spec="me", auth_mode="delegated"))
    names = [(f.id, f.path) for f in out]
    assert ("f1", "Inbox") in names
    assert ("f2", "Sent Items") in names


def test_list_folders_recurses_children():
    graph = _graph_flat_tree(
        root_children=[_folder_raw("inbox", "Inbox", child_count=1, well_known="inbox")],
        sub_map={
            "inbox": [_folder_raw("triage", "Triage", parent="inbox", child_count=1)],
            "triage": [_folder_raw("waiting", "Waiting", parent="triage")],
        },
    )
    out = list(list_folders(graph, mailbox_spec="me", auth_mode="delegated"))
    paths = [f.path for f in out]
    assert "Inbox" in paths
    assert "Inbox/Triage" in paths
    assert "Inbox/Triage/Waiting" in paths
    assert len(out) == 3


def test_list_folders_include_hidden_flag():
    graph = _graph_flat_tree([_folder_raw("f1", "Inbox")])
    list(list_folders(graph, mailbox_spec="me", auth_mode="delegated", include_hidden=True))
    params = graph.get_paginated.call_args.kwargs["params"]
    assert params.get("includeHiddenFolders") == "true"


def test_resolve_folder_path_hits_cached_tree():
    graph = _graph_flat_tree(
        root_children=[_folder_raw("inbox", "Inbox", child_count=1, well_known="inbox")],
        sub_map={"inbox": [_folder_raw("triage", "Triage", parent="inbox")]},
    )
    fid = resolve_folder_path("Inbox/Triage", graph, mailbox_spec="me", auth_mode="delegated")
    assert fid == "triage"


def test_resolve_folder_path_case_insensitive_match():
    graph = _graph_flat_tree([_folder_raw("inbox", "Inbox", well_known="inbox")])
    fid = resolve_folder_path("inbox", graph, mailbox_spec="me", auth_mode="delegated")
    assert fid == "inbox"


def test_resolve_folder_path_missing_raises():
    graph = _graph_flat_tree([_folder_raw("inbox", "Inbox")])
    with pytest.raises(FolderNotFound):
        resolve_folder_path("NonExistent", graph, mailbox_spec="me", auth_mode="delegated")


def test_resolve_folder_path_resolves_well_known_names():
    # Inputs like "inbox" / "sentitems" should resolve to the matching well-known folder.
    graph = _graph_flat_tree([
        _folder_raw("f1", "Inbox", well_known="inbox"),
        _folder_raw("f2", "Sent Items", well_known="sentitems"),
    ])
    assert resolve_folder_path("inbox", graph, mailbox_spec="me", auth_mode="delegated") == "f1"
    assert resolve_folder_path("sentitems", graph, mailbox_spec="me", auth_mode="delegated") == "f2"


def test_get_folder_by_id():
    graph = MagicMock()
    graph.get.return_value = _folder_raw("f1", "Inbox", well_known="inbox")
    f = get_folder(graph, mailbox_spec="me", auth_mode="delegated", folder_id="f1", path="/Inbox")
    assert isinstance(f, Folder)
    assert f.id == "f1"
    assert f.path == "/Inbox"
    assert graph.get.call_args.args[0] == "/me/mailFolders/f1"
```

Run: `uv run pytest tests/test_mail_folders.py -q` → all FAIL.

- [ ] **Step 2: Implement `src/m365ctl/mail/folders.py`.**

```python
"""Read-only folder operations.

Folder listing is recursive by default — the caller gets a flat iterable
of ``Folder`` dataclasses with ``path`` already resolved (e.g. ``"Inbox/Triage"``).
Depth-first traversal; children are requested via ``/mailFolders/{id}/childFolders``
only when ``childFolderCount > 0``.

``resolve_folder_path`` is case-insensitive and accepts well-known names
(``inbox``, ``drafts``, ``sentitems``, ``deleteditems``, ``junkemail``,
``outbox``, ``archive``) directly.
"""
from __future__ import annotations

from typing import Iterator

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.models import Folder


class FolderNotFound(LookupError):
    """Raised when ``resolve_folder_path`` can't find a folder."""


def _walk(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    parent_id: str | None,
    parent_path: str,
    include_hidden: bool,
) -> Iterator[Folder]:
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    if parent_id is None:
        path = f"{ub}/mailFolders"
    else:
        path = f"{ub}/mailFolders/{parent_id}/childFolders"
    params: dict = {"$top": 200}
    if include_hidden:
        params["includeHiddenFolders"] = "true"

    mailbox_upn = mailbox_spec.split(":", 1)[-1] if ":" in mailbox_spec else mailbox_spec
    for items, _ in graph.get_paginated(path, params=params):
        for raw in items:
            disp = raw.get("displayName", "")
            child_path = f"{parent_path}/{disp}" if parent_path else disp
            folder = Folder.from_graph_json(raw, mailbox_upn=mailbox_upn, path=child_path)
            yield folder
            if raw.get("childFolderCount", 0) > 0:
                yield from _walk(
                    graph,
                    mailbox_spec=mailbox_spec,
                    auth_mode=auth_mode,
                    parent_id=raw["id"],
                    parent_path=child_path,
                    include_hidden=include_hidden,
                )


def list_folders(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    include_hidden: bool = False,
) -> Iterator[Folder]:
    """Yield every folder in the mailbox as a flat iterable."""
    yield from _walk(
        graph,
        mailbox_spec=mailbox_spec,
        auth_mode=auth_mode,
        parent_id=None,
        parent_path="",
        include_hidden=include_hidden,
    )


_WELL_KNOWN_NAMES = frozenset({
    "inbox", "drafts", "sentitems", "deleteditems", "junkemail",
    "outbox", "archive",
})


def resolve_folder_path(
    path: str,
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
) -> str:
    """Translate a path like ``"Inbox/Triage"`` to a folder id.

    Also accepts well-known names like ``"inbox"`` (case-insensitive).
    """
    if path.lower() in _WELL_KNOWN_NAMES:
        for folder in list_folders(graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode):
            if (folder.well_known_name or "").lower() == path.lower():
                return folder.id
        raise FolderNotFound(f"well-known folder {path!r} not found in mailbox")

    target = path.strip("/").lower()
    for folder in list_folders(graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode):
        if folder.path.lower() == target:
            return folder.id
    raise FolderNotFound(f"folder path {path!r} not found in mailbox")


def get_folder(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    folder_id: str,
    path: str,
) -> Folder:
    """Fetch a single folder by id."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    raw = graph.get(f"{ub}/mailFolders/{folder_id}")
    mailbox_upn = mailbox_spec.split(":", 1)[-1] if ":" in mailbox_spec else mailbox_spec
    return Folder.from_graph_json(raw, mailbox_upn=mailbox_upn, path=path)
```

- [ ] **Step 3: Run tests + commit.**

```bash
uv run pytest tests/test_mail_folders.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/folders.py tests/test_mail_folders.py
git commit -m "feat(mail): folders readers — list_folders (recursive), resolve_folder_path, get_folder"
```

---

## Group 5: Categories + Rules + Settings + Attachments readers (bundled — small)

### Task 7: `mail/categories.py`

**Files:**
- Create: `src/m365ctl/mail/categories.py`
- Create: `tests/test_mail_categories.py`

- [ ] **Step 1: Failing test.**

Write `tests/test_mail_categories.py`:
```python
from unittest.mock import MagicMock

from m365ctl.mail.categories import list_master_categories
from m365ctl.mail.models import Category


def test_list_master_categories():
    graph = MagicMock()
    graph.get.return_value = {
        "value": [
            {"id": "c1", "displayName": "Followup", "color": "preset0"},
            {"id": "c2", "displayName": "Waiting", "color": "preset4"},
        ]
    }
    out = list_master_categories(graph, mailbox_spec="me", auth_mode="delegated")
    assert out == [
        Category(id="c1", display_name="Followup", color="preset0"),
        Category(id="c2", display_name="Waiting", color="preset4"),
    ]
    assert graph.get.call_args.args[0] == "/me/outlook/masterCategories"


def test_list_master_categories_app_only_routing():
    graph = MagicMock()
    graph.get.return_value = {"value": []}
    list_master_categories(graph, mailbox_spec="upn:bob@example.com", auth_mode="app-only")
    assert graph.get.call_args.args[0] == "/users/bob@example.com/outlook/masterCategories"
```

- [ ] **Step 2: Implement.**

```python
"""Read-only master categories list."""
from __future__ import annotations

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.models import Category


def list_master_categories(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
) -> list[Category]:
    """Return the mailbox's master category list (single non-paginated call)."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    resp = graph.get(f"{ub}/outlook/masterCategories")
    return [Category.from_graph_json(raw) for raw in resp.get("value", [])]
```

- [ ] **Step 3: Run tests + commit.**

```bash
uv run pytest tests/test_mail_categories.py -q
git add src/m365ctl/mail/categories.py tests/test_mail_categories.py
git commit -m "feat(mail): categories.list_master_categories reader"
```

### Task 8: `mail/rules.py`

**Files:**
- Create: `src/m365ctl/mail/rules.py`
- Create: `tests/test_mail_rules.py`

- [ ] **Step 1: Failing test.**

```python
from unittest.mock import MagicMock
from m365ctl.mail.rules import list_rules, get_rule
from m365ctl.mail.models import Rule


def test_list_rules_orders_by_sequence():
    graph = MagicMock()
    graph.get.return_value = {
        "value": [
            {"id": "r1", "displayName": "A", "sequence": 10, "isEnabled": True, "hasError": False, "isReadOnly": False, "conditions": {}, "actions": {}, "exceptions": {}},
            {"id": "r2", "displayName": "B", "sequence": 5,  "isEnabled": True, "hasError": False, "isReadOnly": False, "conditions": {}, "actions": {}, "exceptions": {}},
        ]
    }
    out = list_rules(graph, mailbox_spec="me", auth_mode="delegated")
    assert [r.id for r in out] == ["r2", "r1"]          # by sequence asc
    assert isinstance(out[0], Rule)
    assert graph.get.call_args.args[0] == "/me/mailFolders/inbox/messageRules"


def test_get_rule_single():
    graph = MagicMock()
    graph.get.return_value = {
        "id": "r1", "displayName": "X", "sequence": 1,
        "isEnabled": True, "hasError": False, "isReadOnly": False,
        "conditions": {}, "actions": {}, "exceptions": {},
    }
    r = get_rule(graph, mailbox_spec="me", auth_mode="delegated", rule_id="r1")
    assert r.id == "r1"
    assert graph.get.call_args.args[0] == "/me/mailFolders/inbox/messageRules/r1"
```

- [ ] **Step 2: Implement `src/m365ctl/mail/rules.py`.**

```python
"""Read-only inbox rules list + single-fetch."""
from __future__ import annotations

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.models import Rule


def list_rules(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
) -> list[Rule]:
    """List inbox rules sorted by Graph's ``sequence`` (evaluation order)."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    resp = graph.get(f"{ub}/mailFolders/inbox/messageRules")
    rules = [Rule.from_graph_json(raw) for raw in resp.get("value", [])]
    rules.sort(key=lambda r: r.sequence)
    return rules


def get_rule(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    rule_id: str,
) -> Rule:
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    raw = graph.get(f"{ub}/mailFolders/inbox/messageRules/{rule_id}")
    return Rule.from_graph_json(raw)
```

- [ ] **Step 3: Commit.**

```bash
uv run pytest tests/test_mail_rules.py -q
git add src/m365ctl/mail/rules.py tests/test_mail_rules.py
git commit -m "feat(mail): rules readers — list_rules (sorted by sequence), get_rule"
```

### Task 9: `mail/settings.py`

**Files:**
- Create: `src/m365ctl/mail/settings.py`
- Create: `tests/test_mail_settings.py`

- [ ] **Step 1: Failing test.**

```python
from unittest.mock import MagicMock
from m365ctl.mail.settings import get_settings, get_auto_reply
from m365ctl.mail.models import AutomaticRepliesSetting, MailboxSettings


_SETTINGS_RAW = {
    "timeZone": "Europe/Istanbul",
    "language": {"locale": "en-US", "displayName": "English (United States)"},
    "workingHours": {
        "daysOfWeek": ["monday"],
        "startTime": "09:00:00.0000000",
        "endTime": "17:00:00.0000000",
        "timeZone": {"name": "Europe/Istanbul"},
    },
    "automaticRepliesSetting": {
        "status": "disabled",
        "externalAudience": "none",
        "scheduledStartDateTime": {"dateTime": "2026-04-24T00:00:00.0000000", "timeZone": "UTC"},
        "scheduledEndDateTime":   {"dateTime": "2026-04-24T23:59:59.0000000", "timeZone": "UTC"},
        "internalReplyMessage": "",
        "externalReplyMessage": "",
    },
    "delegateMeetingMessageDeliveryOptions": "sendToDelegateOnly",
    "dateFormat": "yyyy-MM-dd",
    "timeFormat": "HH:mm",
}


def test_get_settings():
    graph = MagicMock()
    graph.get.return_value = _SETTINGS_RAW
    s = get_settings(graph, mailbox_spec="me", auth_mode="delegated")
    assert isinstance(s, MailboxSettings)
    assert s.timezone == "Europe/Istanbul"
    assert graph.get.call_args.args[0] == "/me/mailboxSettings"


def test_get_auto_reply():
    graph = MagicMock()
    graph.get.return_value = _SETTINGS_RAW["automaticRepliesSetting"]
    ar = get_auto_reply(graph, mailbox_spec="me", auth_mode="delegated")
    assert isinstance(ar, AutomaticRepliesSetting)
    assert ar.status == "disabled"
    assert graph.get.call_args.args[0] == "/me/mailboxSettings/automaticRepliesSetting"
```

- [ ] **Step 2: Implement `src/m365ctl/mail/settings.py`.**

```python
"""Read-only mailbox settings + auto-reply fetchers."""
from __future__ import annotations

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.models import AutomaticRepliesSetting, MailboxSettings


def get_settings(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
) -> MailboxSettings:
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    raw = graph.get(f"{ub}/mailboxSettings")
    return MailboxSettings.from_graph_json(raw)


def get_auto_reply(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
) -> AutomaticRepliesSetting:
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    raw = graph.get(f"{ub}/mailboxSettings/automaticRepliesSetting")
    return AutomaticRepliesSetting.from_graph_json(raw)
```

- [ ] **Step 3: Commit.**

```bash
uv run pytest tests/test_mail_settings.py -q
git add src/m365ctl/mail/settings.py tests/test_mail_settings.py
git commit -m "feat(mail): settings readers — get_settings, get_auto_reply"
```

### Task 10: `mail/attachments.py`

**Files:**
- Create: `src/m365ctl/mail/attachments.py`
- Create: `tests/test_mail_attachments.py`

- [ ] **Step 1: Failing test.**

```python
from unittest.mock import MagicMock
import pytest
from m365ctl.mail.attachments import list_attachments, get_attachment_content
from m365ctl.mail.models import Attachment


def test_list_attachments():
    graph = MagicMock()
    graph.get.return_value = {
        "value": [
            {"id": "a1", "@odata.type": "#microsoft.graph.fileAttachment", "name": "x.pdf", "contentType": "application/pdf", "size": 100, "isInline": False},
            {"id": "a2", "@odata.type": "#microsoft.graph.itemAttachment", "name": "y.ics", "contentType": "application/octet-stream", "size": 200, "isInline": False},
        ]
    }
    out = list_attachments(graph, mailbox_spec="me", auth_mode="delegated", message_id="m1")
    assert [a.kind for a in out] == ["file", "item"]
    assert all(isinstance(a, Attachment) for a in out)
    assert graph.get.call_args.args[0] == "/me/messages/m1/attachments"


def test_get_attachment_content_returns_bytes():
    from m365ctl.mail.attachments import get_attachment_content
    graph = MagicMock()
    # The $value endpoint returns raw bytes — tests use the low-level accessor that hits it.
    graph.get_bytes.return_value = b"hello-bytes"
    data = get_attachment_content(graph, mailbox_spec="me", auth_mode="delegated",
                                  message_id="m1", attachment_id="a1")
    assert data == b"hello-bytes"
    assert graph.get_bytes.call_args.args[0] == "/me/messages/m1/attachments/a1/$value"


def test_get_attachment_content_uses_get_bytes_fallback_when_absent(monkeypatch):
    """If GraphClient doesn't have get_bytes yet, fail explicitly — Group 5 will have added it."""
    graph = MagicMock(spec=["get"])    # no get_bytes attribute
    with pytest.raises(AttributeError):
        get_attachment_content(graph, mailbox_spec="me", auth_mode="delegated",
                               message_id="m1", attachment_id="a1")
```

- [ ] **Step 2: Add `GraphClient.get_bytes` and implement `src/m365ctl/mail/attachments.py`.**

First extend `src/m365ctl/common/graph.py` with a `get_bytes` method (after the existing `get`):

```python
def get_bytes(self, path: str) -> bytes:
    """GET with Accept: application/octet-stream; return raw body bytes."""
    def _do() -> bytes:
        resp = self._client.get(path, headers=self._auth_headers())
        self._maybe_raise(resp)
        return resp.content
    return self._retry(_do)
```

Also add a test for `get_bytes` itself in `tests/test_graph.py`:
```python
def test_graph_get_bytes_returns_raw_content(graph_httpx_client_factory):
    # Use whatever fixture test_graph.py already uses to stub httpx responses.
    # Return a 200 with body b"payload" and assert graph.get_bytes("/path") == b"payload".
    ...
```
(If `test_graph.py` doesn't have a reusable fixture, follow its existing patterns for transport mocking. Keep the addition minimal — one passing test for `get_bytes`.)

Then `src/m365ctl/mail/attachments.py`:
```python
"""Read-only attachments list + single-attachment content fetcher."""
from __future__ import annotations

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.models import Attachment


def list_attachments(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    message_id: str,
) -> list[Attachment]:
    """List attachments for ``message_id`` as metadata records (no file bodies)."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    resp = graph.get(f"{ub}/messages/{message_id}/attachments")
    return [Attachment.from_graph_json(raw, message_id=message_id) for raw in resp.get("value", [])]


def get_attachment_content(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    message_id: str,
    attachment_id: str,
) -> bytes:
    """Fetch the raw body of a single attachment (``$value`` endpoint).

    Returns the full payload as bytes. Use for small-to-medium attachments
    (<10 MB). Large-attachment streaming is deferred to a later phase.
    """
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    return graph.get_bytes(f"{ub}/messages/{message_id}/attachments/{attachment_id}/$value")
```

- [ ] **Step 3: Commit.**

```bash
uv run pytest tests/test_mail_attachments.py tests/test_graph.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/common/graph.py src/m365ctl/mail/attachments.py tests/test_graph.py tests/test_mail_attachments.py
git commit -m "feat(mail): attachments readers + GraphClient.get_bytes for raw payloads"
```

---

## Group 6: CLI scaffolding — mail sub-dispatcher + auth/whoami + _common helpers

### Task 11: `mail.cli.__main__` dispatcher + `_common.py` helpers

**Files:**
- Create: `src/m365ctl/mail/cli/__main__.py`
- Create: `src/m365ctl/mail/cli/_common.py`
- Modify: `src/m365ctl/cli/__main__.py` (route `mail` domain to `mail.cli.__main__`)

- [ ] **Step 1: Failing test.**

Append to `tests/test_top_cli.py` (created in Phase 0):
```python
def test_mail_domain_routes_to_mail_cli():
    """`m365ctl mail --help` should no longer print 'not yet implemented'."""
    r = _run(["mail", "--help"])
    # Exit code may be 0 (--help); assert banner mentions `list`, `get`, etc.
    out = r.stdout + r.stderr
    assert "list" in out
    assert "get" in out
    assert "search" in out


def test_mail_list_help_reachable():
    r = _run(["mail", "list", "--help"])
    assert r.returncode == 0
    assert "--mailbox" in r.stdout + r.stderr
```

Run → FAIL.

- [ ] **Step 2: Write the dispatcher.**

`src/m365ctl/mail/cli/__main__.py`:
```python
"""`m365ctl mail <verb>` — reader dispatcher.

Verbs that land in Phase 1:
- auth          device-code login (alias of od-auth; shared cache)
- whoami        identity + scopes + mailbox access summary
- list          list messages in a folder
- get           fetch one message
- search        server-side /search/query
- folders       list folders (tree / flat / with-counts)
- categories    list master categories
- rules         list / show inbox rules
- settings      show mailbox settings
- attach        list / get attachments

Mutation verbs (move, delete, flag, compose, …) land in Phase 2+.
"""
from __future__ import annotations

import sys

_USAGE = (
    "usage: m365ctl mail <verb> [args...]\n"
    "\n"
    "Read-only verbs (Phase 1):\n"
    "  auth         login | whoami\n"
    "  whoami       identity + scopes + mailbox access\n"
    "  list         list messages in a folder\n"
    "  get          fetch a single message\n"
    "  search       server-side message search\n"
    "  folders      list mail folders\n"
    "  categories   list master categories\n"
    "  rules        list / show inbox rules\n"
    "  settings     show mailbox settings\n"
    "  attach       list / get attachments\n"
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(_USAGE)
        return 0 if args else 2
    verb = args[0]
    rest = args[1:]
    # Lazy imports keep startup fast — only the dispatched verb is loaded.
    if verb == "auth":
        from m365ctl.mail.cli.auth import main as f
    elif verb == "whoami":
        from m365ctl.mail.cli.whoami import main as f
    elif verb == "list":
        from m365ctl.mail.cli.list import main as f
    elif verb == "get":
        from m365ctl.mail.cli.get import main as f
    elif verb == "search":
        from m365ctl.mail.cli.search import main as f
    elif verb == "folders":
        from m365ctl.mail.cli.folders import main as f
    elif verb == "categories":
        from m365ctl.mail.cli.categories import main as f
    elif verb == "rules":
        from m365ctl.mail.cli.rules import main as f
    elif verb == "settings":
        from m365ctl.mail.cli.settings import main as f
    elif verb == "attach":
        from m365ctl.mail.cli.attach import main as f
    else:
        print(f"m365ctl mail: unknown verb {verb!r}\n\n{_USAGE}", file=sys.stderr)
        return 2
    return f(rest) or 0
```

`src/m365ctl/mail/cli/_common.py`:
```python
"""Shared CLI helpers for `m365ctl mail <verb>` commands.

Surface:
- ``add_common_args(parser)`` — adds ``--config``, ``--mailbox``, ``--json``.
- ``resolve_mailbox_and_auth(cfg, mailbox, *, force_app_only=False)`` —
  validates the spec is allowed, returns ``(auth_mode, credential)``.
- ``emit(records, as_json)`` — print either human-readable or NDJSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from m365ctl.common.auth import AppOnlyCredential, DelegatedCredential
from m365ctl.common.config import Config, load_config
from m365ctl.common.safety import assert_mailbox_allowed


def add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", default="config.toml", help="Path to config.toml (default: config.toml).")
    p.add_argument("--mailbox", default="me",
                   help="Mailbox: 'me' | 'upn:<addr>' | 'shared:<addr>' | '*' (default: me).")
    p.add_argument("--json", action="store_true", help="Emit NDJSON instead of human-readable output.")
    p.add_argument("--unsafe-scope", action="store_true",
                   help="Override allow_mailboxes via /dev/tty confirm (per mailbox).")


def load_and_authorize(args: argparse.Namespace) -> tuple[Config, str, Any]:
    """Load config, gate the requested mailbox, and return (cfg, auth_mode, credential).

    ``auth_mode`` is "delegated" when ``--mailbox me`` AND ``default_auth == 'delegated'``;
    otherwise "app-only" so cross-mailbox calls use the certificate flow.
    """
    cfg = load_config(Path(args.config))
    mailbox_spec = args.mailbox
    # Pick auth mode: me → delegated (default); upn/shared → app-only.
    if mailbox_spec == "me":
        auth_mode = cfg.default_auth  # typically "delegated"
    else:
        auth_mode = "app-only"
    assert_mailbox_allowed(mailbox_spec, cfg, auth_mode=auth_mode, unsafe_scope=args.unsafe_scope)
    cred = DelegatedCredential(cfg) if auth_mode == "delegated" else AppOnlyCredential(cfg)
    return cfg, auth_mode, cred


def _json_default(o: Any) -> Any:
    if is_dataclass(o):
        return asdict(o)
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, bytes):
        import base64
        return base64.b64encode(o).decode("ascii")
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"not JSON-serializable: {type(o).__name__}")


def emit_json_lines(records: Iterable[Any]) -> None:
    for rec in records:
        sys.stdout.write(json.dumps(rec, default=_json_default, ensure_ascii=False))
        sys.stdout.write("\n")
```

- [ ] **Step 3: Wire `mail` domain into the top-level dispatcher.**

Open `src/m365ctl/cli/__main__.py` and replace the `mail` branch body:
```python
    if domain == "mail":
        from m365ctl.mail.cli.__main__ import main as mail_main
        return mail_main(rest) or 0
```
(Previously this branch printed "not yet implemented" and returned 2.)

Also update the existing top-cli test that asserted the "not yet" behavior:
```python
def test_mail_domain_exists_but_has_no_verbs_yet():
    ...
```
Either rewrite this test to assert the new routing behavior, or delete it if it's now redundant with `test_mail_domain_routes_to_mail_cli` added in Step 1.

Recommended rewrite:
```python
def test_mail_domain_no_verb_prints_usage():
    # With no verb: mail dispatcher prints its own usage and exits 2.
    r = _run(["mail"])
    assert r.returncode != 0
    out = (r.stdout + r.stderr).lower()
    assert "verb" in out or "usage" in out
```

- [ ] **Step 4: Create stub module files so the dispatcher imports cleanly during tests.**

For each lazy import in `mail/cli/__main__.py` to succeed, each referenced verb module must exist. Create minimal stubs for now (Tasks 12–17 fill them):

Each of these files: `src/m365ctl/mail/cli/{auth,whoami,list,get,search,folders,categories,rules,settings,attach}.py` should contain:
```python
"""Stub for Group N task."""
from __future__ import annotations
import sys


def main(argv: list[str]) -> int:
    print(f"m365ctl mail: verb not yet implemented (this task lands in a later Phase 1 group)", file=sys.stderr)
    return 2
```

Tasks 12–17 replace each stub with the real implementation.

- [ ] **Step 5: Run tests + commit.**

```bash
uv run pytest tests/test_top_cli.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/cli/__main__.py src/m365ctl/mail/cli/ tests/test_top_cli.py
git commit -m "feat(mail/cli): sub-dispatcher + _common helpers; route m365ctl mail → mail.cli"
```

---

### Task 12: `mail-auth` + `mail-whoami`

**Files:**
- Modify (replace stubs): `src/m365ctl/mail/cli/auth.py`, `src/m365ctl/mail/cli/whoami.py`
- Create: `tests/test_cli_mail_whoami.py`

- [ ] **Step 1: `mail/cli/auth.py` — thin delegate.**

```python
"""`m365ctl mail auth login|whoami` — shares the same token cache as od-auth."""
from __future__ import annotations

from m365ctl.onedrive.cli.auth import main as od_auth_main


def main(argv: list[str]) -> int:
    # Shared delegated cache means mail-auth login === od-auth login.
    return od_auth_main(argv)
```

- [ ] **Step 2: `mail/cli/whoami.py` — identity + scopes + mailbox probe.**

```python
"""`m365ctl mail whoami` — identity, scopes, mailbox access, catalog stub."""
from __future__ import annotations

import argparse
from pathlib import Path

from m365ctl.common.auth import (
    AppOnlyCredential,
    AuthError,
    DelegatedCredential,
    GRAPH_SCOPES_DELEGATED,
)
from m365ctl.common.config import load_config
from m365ctl.common.graph import GraphClient


_ENTRA_CONSENT_URL_TEMPLATE = (
    "https://login.microsoftonline.com/{tenant}/adminconsent?client_id={client}"
)
_REQUIRED_MAIL_SCOPES = ("Mail.ReadWrite", "Mail.Send", "MailboxSettings.ReadWrite")


def run_whoami(config_path: Path) -> int:
    cfg = load_config(config_path)

    print("m365ctl mail")
    print("============")
    print(f"Tenant:                {cfg.tenant_id}")

    # Declared scopes.
    missing: list[str] = []
    for s in _REQUIRED_MAIL_SCOPES:
        if s not in GRAPH_SCOPES_DELEGATED:
            missing.append(s)
    print(f"Declared delegated scopes: {len(GRAPH_SCOPES_DELEGATED)} total")
    if missing:
        print(f"  MISSING in code: {', '.join(missing)}")
        return 2  # Phase 0/Task 1 regression

    # Delegated probe: hit /me/mailFolders/inbox.
    delegated = DelegatedCredential(cfg)
    try:
        token = delegated.get_token()
        graph = GraphClient(token_provider=lambda: token)
        me = graph.get("/me")
        print(f"Delegated identity:    {me.get('displayName', '?')} <{me.get('userPrincipalName', '?')}>")
        try:
            inbox = graph.get("/me/mailFolders/inbox")
            print(f"Mail access (me):      OK — /Inbox totals "
                  f"{inbox.get('totalItemCount', 0)} items, {inbox.get('unreadItemCount', 0)} unread")
        except Exception as e:  # GraphError / httpx failure
            msg = str(e)
            print(f"Mail access (me):      FAILED — {msg.splitlines()[0] if msg else e!r}")
            if "403" in msg or "AccessDenied" in msg or "consent" in msg.lower():
                consent_url = _ENTRA_CONSENT_URL_TEMPLATE.format(
                    tenant=cfg.tenant_id, client=cfg.client_id,
                )
                print(f"  Remediation: grant admin consent at:\n    {consent_url}")
    except AuthError as e:
        print(f"Delegated identity:    (not available - {e})")

    # App-only status (surface-only).
    try:
        app_only = AppOnlyCredential(cfg)
        info = app_only.cert_info
        print(f"App-only cert:         {info.subject}, thumbprint {info.thumbprint}, "
              f"expires {info.not_after_utc} ({info.days_until_expiry} days)")
    except Exception as e:
        print(f"App-only cert:         (not available - {e})")

    # Mail catalog: Phase 7 — print stub.
    print(f"Mail catalog:          (not yet built — Phase 7)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail whoami")
    p.add_argument("--config", default="config.toml")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_whoami(Path(args.config))
```

- [ ] **Step 3: Unit test for whoami.**

Write `tests/test_cli_mail_whoami.py`:
```python
"""Subprocess smoke + scope-presence assertion for `m365ctl mail whoami`."""
from __future__ import annotations

from pathlib import Path

from m365ctl.common.auth import GRAPH_SCOPES_DELEGATED
from m365ctl.mail.cli.whoami import _REQUIRED_MAIL_SCOPES


def test_required_scopes_are_declared():
    # Runs even without a real tenant — asserts the module constants agree.
    for s in _REQUIRED_MAIL_SCOPES:
        assert s in GRAPH_SCOPES_DELEGATED


def test_whoami_help_parses(tmp_path, capsys):
    from m365ctl.mail.cli.whoami import build_parser
    args = build_parser().parse_args(["--config", str(tmp_path / "config.toml")])
    assert args.config == str(tmp_path / "config.toml")
```

- [ ] **Step 4: Commit.**

```bash
uv run pytest tests/test_cli_mail_whoami.py -q
uv run pytest -m "not live" -q 2>&1 | tail -3
git add src/m365ctl/mail/cli/auth.py src/m365ctl/mail/cli/whoami.py tests/test_cli_mail_whoami.py
git commit -m "feat(mail/cli): mail-auth (alias) + mail-whoami with scope + mailbox probe + consent URL"
```

---

## Group 7: `mail list` + `mail get` verbs

### Task 13: `mail list`

**Files:**
- Modify: `src/m365ctl/mail/cli/list.py`
- Create: `tests/test_cli_mail_list.py`

- [ ] **Step 1: Implementation (tests in Step 2).**

```python
"""`m365ctl mail list` — list messages in a folder with OData filters."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from m365ctl.common.graph import GraphClient
from m365ctl.mail.cli._common import (
    _json_default,
    add_common_args,
    emit_json_lines,
    load_and_authorize,
)
from m365ctl.mail.folders import FolderNotFound, resolve_folder_path
from m365ctl.mail.messages import MessageListFilters, list_messages


def _print_human(messages) -> None:
    for m in messages:
        flag = "!" if m.flag.status == "flagged" else " "
        unread = "U" if not m.is_read else " "
        sender = m.from_addr.address or m.sender.address
        print(f"{flag}{unread} {m.received_at.isoformat(timespec='minutes')}  {sender:<40}  {m.subject}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail list")
    add_common_args(p)
    p.add_argument("--folder", default="Inbox", help="Folder path or well-known name (default: Inbox).")
    p.add_argument("--from", dest="from_address", help="Filter by sender address (exact match).")
    p.add_argument("--subject", dest="subject_contains", help="Filter by substring in subject.")
    p.add_argument("--since", help="ISO-8601 lower bound on receivedDateTime.")
    p.add_argument("--until", help="ISO-8601 upper bound on receivedDateTime.")
    p.add_argument("--unread", action="store_true", help="Only unread messages.")
    p.add_argument("--read", action="store_true", help="Only already-read messages.")
    p.add_argument("--has-attachments", action="store_true")
    p.add_argument("--importance", choices=("low", "normal", "high"))
    p.add_argument("--focus", choices=("focused", "other"))
    p.add_argument("--category", help="Filter by category name (exact match on one entry).")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--page-size", type=int, default=50)
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    # Resolve folder path → id.
    try:
        folder_id = resolve_folder_path(
            args.folder, graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
    except FolderNotFound as e:
        print(f"mail list: {e}", file=sys.stderr)
        return 2

    # Unread/read resolution.
    unread_flag: bool | None = None
    if args.unread and args.read:
        print("mail list: --unread and --read are mutually exclusive", file=sys.stderr)
        return 2
    if args.unread:
        unread_flag = True
    elif args.read:
        unread_flag = False

    filters = MessageListFilters(
        unread=unread_flag,
        from_address=args.from_address,
        subject_contains=args.subject_contains,
        since=args.since,
        until=args.until,
        has_attachments=args.has_attachments or None,
        importance=args.importance,
        focus=args.focus,
        category=args.category,
    )

    msgs = list_messages(
        graph,
        mailbox_spec=args.mailbox,
        auth_mode=auth_mode,
        folder_id=folder_id,
        parent_folder_path=args.folder,
        filters=filters,
        limit=args.limit,
        page_size=args.page_size,
    )

    if args.json:
        emit_json_lines(msgs)
    else:
        _print_human(msgs)
    return 0
```

- [ ] **Step 2: Tests.**

Write `tests/test_cli_mail_list.py`:
```python
from m365ctl.mail.cli.list import build_parser


def test_mail_list_parser_defaults():
    args = build_parser().parse_args([])
    assert args.folder == "Inbox"
    assert args.limit == 50
    assert args.page_size == 50
    assert not args.unread
    assert not args.read
    assert args.json is False
    assert args.mailbox == "me"


def test_mail_list_parser_filters():
    args = build_parser().parse_args([
        "--folder", "Archive/2026",
        "--from", "alice@example.com",
        "--subject", "meeting",
        "--since", "2026-04-20T00:00:00Z",
        "--until", "2026-04-24T00:00:00Z",
        "--unread",
        "--has-attachments",
        "--importance", "high",
        "--focus", "focused",
        "--category", "Followup",
        "--limit", "25",
        "--json",
    ])
    assert args.folder == "Archive/2026"
    assert args.from_address == "alice@example.com"
    assert args.subject_contains == "meeting"
    assert args.unread is True
    assert args.has_attachments is True
    assert args.importance == "high"
    assert args.focus == "focused"
    assert args.category == "Followup"
    assert args.limit == 25
    assert args.json is True
```

- [ ] **Step 3: Commit.**

```bash
uv run pytest tests/test_cli_mail_list.py -q
git add src/m365ctl/mail/cli/list.py tests/test_cli_mail_list.py
git commit -m "feat(mail/cli): mail-list with OData filter args and --json emission"
```

### Task 14: `mail get`

**Files:**
- Modify: `src/m365ctl/mail/cli/get.py`
- Create: `tests/test_cli_mail_get.py`

- [ ] **Step 1: Implementation.**

```python
"""`m365ctl mail get <message-id>` — fetch one message."""
from __future__ import annotations

import argparse
import sys

from m365ctl.common.graph import GraphClient
from m365ctl.mail.cli._common import (
    add_common_args,
    emit_json_lines,
    load_and_authorize,
)
from m365ctl.mail.messages import get_message


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail get")
    add_common_args(p)
    p.add_argument("message_id", help="Graph message id (from mail-list).")
    p.add_argument("--with-body", action="store_true", help="Include message body.")
    p.add_argument("--with-headers", action="store_true", help="Include raw Internet headers.")
    p.add_argument("--with-attachments", action="store_true", help="Expand attachments list.")
    p.add_argument("--eml", action="store_true",
                   help="Emit as .eml (raw mime) instead of JSON/text. (Deferred — stub in Phase 1.)")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.eml:
        print("mail get --eml: deferred to Phase 11 (export).", file=sys.stderr)
        return 2

    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    msg = get_message(
        graph,
        mailbox_spec=args.mailbox,
        auth_mode=auth_mode,
        message_id=args.message_id,
        with_attachments=args.with_attachments,
    )

    if args.json:
        emit_json_lines([msg])
    else:
        print(f"id:          {msg.id}")
        print(f"subject:     {msg.subject}")
        print(f"from:        {msg.from_addr.address}")
        print(f"to:          {', '.join(a.address for a in msg.to)}")
        print(f"received:    {msg.received_at.isoformat()}")
        print(f"folder:      {msg.parent_folder_path}")
        print(f"flag:        {msg.flag.status}")
        print(f"read:        {msg.is_read}")
        if args.with_body and msg.body:
            print(f"body-type:   {msg.body.content_type}")
            print("body:")
            print(msg.body.content)
    return 0
```

- [ ] **Step 2: Tests.**

Write `tests/test_cli_mail_get.py`:
```python
from m365ctl.mail.cli.get import build_parser, main


def test_mail_get_parser_requires_message_id():
    import pytest
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_mail_get_parser_accepts_flags():
    args = build_parser().parse_args([
        "AAMkAD.mm=",
        "--with-body", "--with-attachments", "--json",
    ])
    assert args.message_id == "AAMkAD.mm="
    assert args.with_body
    assert args.with_attachments
    assert args.json


def test_mail_get_eml_flag_returns_deferred_exit():
    # --eml prints a deferral notice and returns 2 without touching Graph.
    rc = main(["--config", "/nonexistent", "abc", "--eml"])
    assert rc == 2
```

- [ ] **Step 3: Commit.**

```bash
uv run pytest tests/test_cli_mail_get.py -q
git add src/m365ctl/mail/cli/get.py tests/test_cli_mail_get.py
git commit -m "feat(mail/cli): mail-get with --with-body/--with-attachments/--json; --eml deferred to Phase 11"
```

---

## Group 8: `mail search` + `mail folders` verbs

### Task 15: `mail search`

**Files:**
- Modify: `src/m365ctl/mail/cli/search.py`
- Create: `tests/test_cli_mail_search.py`

- [ ] **Step 1: Implementation.**

```python
"""`m365ctl mail search <query>` — server-side /search/query over messages."""
from __future__ import annotations

import argparse
import sys

from m365ctl.common.graph import GraphClient
from m365ctl.mail.cli._common import add_common_args, emit_json_lines, load_and_authorize
from m365ctl.mail.messages import search_messages_graph


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail search")
    add_common_args(p)
    p.add_argument("query", help='Search expression (KQL: from:alice AND subject:meeting).')
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--local", action="store_true", help="Query the local DuckDB catalog (Phase 7).")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.local:
        print("mail search --local: catalog arrives in Phase 7.", file=sys.stderr)
        return 2

    _cfg, _auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    hits = list(search_messages_graph(graph, query=args.query, limit=args.limit))

    if args.json:
        emit_json_lines(hits)
    else:
        for m in hits:
            sender = m.from_addr.address or m.sender.address
            print(f"{m.received_at.isoformat(timespec='minutes')}  {sender:<40}  {m.subject}")
    return 0
```

- [ ] **Step 2: Tests.**

`tests/test_cli_mail_search.py`:
```python
from m365ctl.mail.cli.search import build_parser, main


def test_mail_search_parser_defaults():
    args = build_parser().parse_args(["invoice"])
    assert args.query == "invoice"
    assert args.limit == 25
    assert not args.local


def test_mail_search_local_defers_to_phase_7():
    rc = main(["--config", "/nonexistent", "query", "--local"])
    assert rc == 2
```

- [ ] **Step 3: Commit.**

```bash
uv run pytest tests/test_cli_mail_search.py -q
git add src/m365ctl/mail/cli/search.py tests/test_cli_mail_search.py
git commit -m "feat(mail/cli): mail-search (Graph /search/query); --local deferred to Phase 7"
```

### Task 16: `mail folders`

**Files:**
- Modify: `src/m365ctl/mail/cli/folders.py`
- Create: `tests/test_cli_mail_folders.py`

- [ ] **Step 1: Implementation.**

```python
"""`m365ctl mail folders` — list / tree view of mail folders."""
from __future__ import annotations

import argparse

from m365ctl.common.graph import GraphClient
from m365ctl.common.safety import is_folder_denied
from m365ctl.mail.cli._common import add_common_args, emit_json_lines, load_and_authorize
from m365ctl.mail.folders import list_folders


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail folders")
    add_common_args(p)
    p.add_argument("--tree", action="store_true", help="Tree view (indent by depth).")
    p.add_argument("--with-counts", action="store_true", help="Show total/unread counts.")
    p.add_argument("--include-hidden", action="store_true", help="Include hidden folders.")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    folders = list(list_folders(
        graph,
        mailbox_spec=args.mailbox,
        auth_mode=auth_mode,
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
```

- [ ] **Step 2: Tests.**

`tests/test_cli_mail_folders.py`:
```python
from m365ctl.mail.cli.folders import build_parser


def test_mail_folders_parser_defaults():
    args = build_parser().parse_args([])
    assert args.tree is False
    assert args.with_counts is False
    assert args.include_hidden is False


def test_mail_folders_parser_flags():
    args = build_parser().parse_args(["--tree", "--with-counts", "--include-hidden", "--json"])
    assert args.tree is True
    assert args.with_counts is True
    assert args.include_hidden is True
    assert args.json is True
```

- [ ] **Step 3: Commit.**

```bash
uv run pytest tests/test_cli_mail_folders.py -q
git add src/m365ctl/mail/cli/folders.py tests/test_cli_mail_folders.py
git commit -m "feat(mail/cli): mail-folders with --tree/--with-counts/--include-hidden and deny-folder filter"
```

---

## Group 9: `mail categories` + `mail rules` + `mail settings` + `mail attach` verbs

### Task 17: `mail categories` (list only in Phase 1)

**Files:**
- Modify: `src/m365ctl/mail/cli/categories.py`
- Create: `tests/test_cli_mail_categories.py`

- [ ] **Step 1: Implementation.**

```python
"""`m365ctl mail categories list` — list master categories (read-only)."""
from __future__ import annotations

import argparse

from m365ctl.common.graph import GraphClient
from m365ctl.mail.categories import list_master_categories
from m365ctl.mail.cli._common import add_common_args, emit_json_lines, load_and_authorize


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail categories")
    add_common_args(p)
    sub = p.add_subparsers(dest="subcommand", required=False)
    sub.add_parser("list", help="List master categories (default).")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
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
```

- [ ] **Step 2: Tests + commit.**

`tests/test_cli_mail_categories.py`:
```python
from m365ctl.mail.cli.categories import build_parser


def test_mail_categories_parser():
    args = build_parser().parse_args([])
    assert args.subcommand is None
    args = build_parser().parse_args(["list"])
    assert args.subcommand == "list"
```

```bash
uv run pytest tests/test_cli_mail_categories.py -q
git add src/m365ctl/mail/cli/categories.py tests/test_cli_mail_categories.py
git commit -m "feat(mail/cli): mail-categories list (read-only; CRUD lands Phase 2)"
```

### Task 18: `mail rules` (list + show only in Phase 1)

**Files:**
- Modify: `src/m365ctl/mail/cli/rules.py`
- Create: `tests/test_cli_mail_rules.py`

- [ ] **Step 1: Implementation.**

```python
"""`m365ctl mail rules list|show` — read-only inbox rules (CRUD lands Phase 8)."""
from __future__ import annotations

import argparse

from m365ctl.common.graph import GraphClient
from m365ctl.mail.cli._common import add_common_args, emit_json_lines, load_and_authorize
from m365ctl.mail.rules import get_rule, list_rules


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail rules")
    add_common_args(p)
    sub = p.add_subparsers(dest="subcommand", required=True)
    lst = sub.add_parser("list", help="List rules by evaluation order.")
    lst.add_argument("--disabled", action="store_true",
                     help="Show disabled rules too (default: enabled only).")
    show = sub.add_parser("show", help="Show a single rule.")
    show.add_argument("rule_id")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    if args.subcommand == "list":
        rules = list_rules(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)
        if not args.disabled:
            rules = [r for r in rules if r.is_enabled]
        if args.json:
            emit_json_lines(rules)
        else:
            for r in rules:
                enabled = "✓" if r.is_enabled else "✗"
                print(f"{r.sequence:<4} {enabled}  {r.display_name}  (id: {r.id})")
        return 0

    if args.subcommand == "show":
        rule = get_rule(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode, rule_id=args.rule_id)
        if args.json:
            emit_json_lines([rule])
        else:
            print(f"id:          {rule.id}")
            print(f"name:        {rule.display_name}")
            print(f"sequence:    {rule.sequence}")
            print(f"enabled:     {rule.is_enabled}")
            print(f"has_error:   {rule.has_error}")
            print(f"read_only:   {rule.is_read_only}")
            print(f"conditions:  {rule.conditions}")
            print(f"actions:     {rule.actions}")
            print(f"exceptions:  {rule.exceptions}")
        return 0
    return 2
```

- [ ] **Step 2: Tests + commit.**

`tests/test_cli_mail_rules.py`:
```python
import pytest
from m365ctl.mail.cli.rules import build_parser


def test_mail_rules_list_parser():
    args = build_parser().parse_args(["list"])
    assert args.subcommand == "list"
    assert args.disabled is False


def test_mail_rules_show_requires_id():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["show"])
    args = build_parser().parse_args(["show", "rule-id"])
    assert args.subcommand == "show"
    assert args.rule_id == "rule-id"
```

```bash
uv run pytest tests/test_cli_mail_rules.py -q
git add src/m365ctl/mail/cli/rules.py tests/test_cli_mail_rules.py
git commit -m "feat(mail/cli): mail-rules list/show (read-only; CRUD lands Phase 8)"
```

### Task 19: `mail settings`

**Files:**
- Modify: `src/m365ctl/mail/cli/settings.py`
- Create: `tests/test_cli_mail_settings.py`

- [ ] **Step 1: Implementation.**

```python
"""`m365ctl mail settings show` — read-only mailbox settings."""
from __future__ import annotations

import argparse

from m365ctl.common.graph import GraphClient
from m365ctl.mail.cli._common import add_common_args, emit_json_lines, load_and_authorize
from m365ctl.mail.settings import get_auto_reply, get_settings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail settings")
    add_common_args(p)
    sub = p.add_subparsers(dest="subcommand", required=True)
    sub.add_parser("show", help="Print all mailbox settings.")
    sub.add_parser("ooo", help="Print the automatic-replies setting.")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    if args.subcommand == "show":
        s = get_settings(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)
        if args.json:
            emit_json_lines([s])
        else:
            print(f"timezone:       {s.timezone}")
            print(f"language:       {s.language.locale} ({s.language.display_name})")
            wh = s.working_hours
            print(f"working_hours:  {','.join(wh.days)} {wh.start_time}-{wh.end_time} {wh.time_zone}")
            print(f"auto_reply:     {s.auto_reply.status} (audience: {s.auto_reply.external_audience})")
            print(f"delegate_msgs:  {s.delegate_meeting_message_delivery}")
            print(f"date_format:    {s.date_format}")
            print(f"time_format:    {s.time_format}")
        return 0

    if args.subcommand == "ooo":
        ar = get_auto_reply(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode)
        if args.json:
            emit_json_lines([ar])
        else:
            print(f"status:             {ar.status}")
            print(f"external_audience:  {ar.external_audience}")
            print(f"scheduled_start:    {ar.scheduled_start}")
            print(f"scheduled_end:      {ar.scheduled_end}")
            print(f"internal_reply:     {ar.internal_reply_message!r}")
            print(f"external_reply:     {ar.external_reply_message!r}")
        return 0
    return 2
```

- [ ] **Step 2: Tests + commit.**

`tests/test_cli_mail_settings.py`:
```python
from m365ctl.mail.cli.settings import build_parser


def test_mail_settings_parser():
    args = build_parser().parse_args(["show"])
    assert args.subcommand == "show"
    args = build_parser().parse_args(["ooo"])
    assert args.subcommand == "ooo"
```

```bash
uv run pytest tests/test_cli_mail_settings.py -q
git add src/m365ctl/mail/cli/settings.py tests/test_cli_mail_settings.py
git commit -m "feat(mail/cli): mail-settings show + ooo (read-only; set lands Phase 9)"
```

### Task 20: `mail attach`

**Files:**
- Modify: `src/m365ctl/mail/cli/attach.py`
- Create: `tests/test_cli_mail_attach.py`

- [ ] **Step 1: Implementation.**

```python
"""`m365ctl mail attach list|get` — read-only attachments."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.graph import GraphClient
from m365ctl.mail.attachments import get_attachment_content, list_attachments
from m365ctl.mail.cli._common import add_common_args, emit_json_lines, load_and_authorize


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail attach")
    add_common_args(p)
    sub = p.add_subparsers(dest="subcommand", required=True)
    lst = sub.add_parser("list")
    lst.add_argument("message_id")
    get = sub.add_parser("get")
    get.add_argument("message_id")
    get.add_argument("attachment_id")
    get.add_argument("--out", help="Path to write the attachment to. Default: stdout.")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    if args.subcommand == "list":
        out = list_attachments(
            graph, mailbox_spec=args.mailbox, auth_mode=auth_mode, message_id=args.message_id,
        )
        if args.json:
            emit_json_lines(out)
        else:
            for a in out:
                print(f"{a.kind:<9}  {a.size:>10}  {a.content_type:<40}  {a.name}  (id: {a.id})")
        return 0

    if args.subcommand == "get":
        data = get_attachment_content(
            graph,
            mailbox_spec=args.mailbox,
            auth_mode=auth_mode,
            message_id=args.message_id,
            attachment_id=args.attachment_id,
        )
        if args.out:
            Path(args.out).write_bytes(data)
            print(f"Wrote {len(data)} bytes to {args.out}", file=sys.stderr)
        else:
            sys.stdout.buffer.write(data)
        return 0

    return 2
```

- [ ] **Step 2: Tests + commit.**

`tests/test_cli_mail_attach.py`:
```python
import pytest
from m365ctl.mail.cli.attach import build_parser


def test_attach_list_requires_message_id():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["list"])
    args = build_parser().parse_args(["list", "msg-id"])
    assert args.subcommand == "list"
    assert args.message_id == "msg-id"


def test_attach_get_requires_both_ids():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["get", "msg-id"])
    args = build_parser().parse_args(["get", "msg-id", "att-id", "--out", "/tmp/x"])
    assert args.subcommand == "get"
    assert args.message_id == "msg-id"
    assert args.attachment_id == "att-id"
    assert args.out == "/tmp/x"
```

```bash
uv run pytest tests/test_cli_mail_attach.py -q
git add src/m365ctl/mail/cli/attach.py tests/test_cli_mail_attach.py
git commit -m "feat(mail/cli): mail-attach list/get (read-only; add/remove lands Phase 5a)"
```

---

## Group 10: Bin wrappers + scope-violation end-to-end test

### Task 21: Add 10 `bin/mail-*` wrappers

**Files:** `bin/mail-{auth,whoami,list,get,search,folders,categories,rules,settings,attach}` — all new.

- [ ] **Step 1: Write one wrapper exemplar, then apply to all.**

Each wrapper follows this shape:
```bash
#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec uv run --project "$REPO" python -m m365ctl mail VERB "$@"
```

For `bin/mail-list`:
```bash
#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec uv run --project "$REPO" python -m m365ctl mail list "$@"
```

Create each of the 10 wrappers with `VERB` replaced by: `auth`, `whoami`, `list`, `get`, `search`, `folders`, `categories`, `rules`, `settings`, `attach`.

- [ ] **Step 2: Make all executable.**

```bash
chmod +x bin/mail-*
```

- [ ] **Step 3: Smoke.**

```bash
./bin/mail-list --help 2>&1 | head -5
./bin/mail-folders --help 2>&1 | head -5
```
Expected: argparse help banners, no ImportError.

- [ ] **Step 4: Commit.**

```bash
git add bin/mail-*
git commit -m "feat(bin): add mail-auth/whoami/list/get/search/folders/categories/rules/settings/attach wrappers"
```

### Task 22: End-to-end scope-violation test

**Files:** `tests/test_mail_safety.py` — append.

- [ ] **Step 1: Failing test.**

Append to `tests/test_mail_safety.py`:
```python
def test_mail_list_fails_fast_when_mailbox_not_in_allow_list(tmp_path, capsys):
    """Scope enforcement: listing a mailbox outside allow_mailboxes fails BEFORE any Graph call."""
    from m365ctl.mail.cli.list import main
    # Write a minimal config with allow_mailboxes=["me"].
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
    # Pass a mailbox NOT in the allow list; no tty → ScopeViolation bubbles up.
    # The CLI should translate ScopeViolation into an error exit (not a traceback
    # of uncaught ScopeViolation). For Phase 1 we expect the exception to escape;
    # a future change may wrap it in the CLI layer.
    import pytest
    from m365ctl.common.safety import ScopeViolation
    with pytest.raises(ScopeViolation):
        main(["--config", str(cfg_path), "--mailbox", "upn:other@example.com"])
```

- [ ] **Step 2: Run + commit.**

```bash
uv run pytest tests/test_mail_safety.py -q
git add tests/test_mail_safety.py
git commit -m "test(mail/safety): e2e — mail-list raises ScopeViolation for mailbox not in allow_mailboxes"
```

---

## Group 11: Version bump + CHANGELOG + docs/setup

### Task 23: Bump to 0.2.0 + CHANGELOG entry

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Bump version.**

In `pyproject.toml`, change `version = "0.1.0"` → `version = "0.2.0"`.

- [ ] **Step 2: Add 0.2.0 CHANGELOG entry under `[Unreleased]`.**

```markdown
## [0.2.0] — 2026-04-24

### Added
- **Mail domain reader surface.**
  - `m365ctl mail list` — OData-filtered message list (folder, unread, from, subject, since/until, importance, focus, category, has-attachments).
  - `m365ctl mail get` — fetch one message, optionally with body and attachments.
  - `m365ctl mail search` — server-side `/search/query` over mail entity.
  - `m365ctl mail folders` — tree/flat folder list with counts; hard-coded deny list filters out compliance buckets.
  - `m365ctl mail categories` — master category list.
  - `m365ctl mail rules` — inbox rule list/show.
  - `m365ctl mail settings` — mailbox settings + OOO view.
  - `m365ctl mail attach` — list/get attachments.
  - `m365ctl mail whoami` — identity, declared scopes, delegated probe, cert expiry, catalog stub. Surfaces admin-consent URL on 403.
- `m365ctl.mail.models` — frozen dataclasses with `from_graph_json` parsers for Message, Folder, Category, Rule, Attachment, MailboxSettings + children.
- `m365ctl.common.safety.assert_mailbox_allowed` + `is_folder_denied` with hardcoded compliance deny list.
- `GraphClient.get_bytes(path)` — raw byte fetch for attachment content.
- `bin/mail-*` short wrappers (10 new).

### Changed
- `GRAPH_SCOPES_DELEGATED` now includes `Mail.ReadWrite`, `Mail.Send`, `MailboxSettings.ReadWrite`. **Requires admin re-consent** on the Entra app.
- Top-level CLI dispatcher now routes `m365ctl mail` to the mail domain instead of the Phase 0 "not yet implemented" stub.

### Migration
- Grant admin consent for the three new delegated scopes. Users who ran `od-auth login` before 0.2.0 must re-run it after consent (device-code flow picks up the expanded scope set).
```

Also move any existing `## [Unreleased]` section above 0.2.0 (empty or containing post-0.2.0 work).

- [ ] **Step 3: Commit.**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore(release): bump to 0.2.0 + CHANGELOG entry for mail readers"
```

### Task 24: Update `docs/setup/azure-app-registration.md` with mail permissions

**Files:** `docs/setup/azure-app-registration.md`.

- [ ] **Step 1: Extend the permissions section.**

Locate the API permissions section and add the three mail delegated scopes + three mail application scopes to the existing tables. Preserve the existing OneDrive entries.

The result should list:

**Delegated:**
- Files.ReadWrite.All
- Sites.ReadWrite.All
- User.Read
- **Mail.ReadWrite** (NEW — Phase 1)
- **Mail.Send** (NEW — Phase 1, reserved for Phase 5a send)
- **MailboxSettings.ReadWrite** (NEW — Phase 1)

**Application:**
- Files.ReadWrite.All
- Sites.ReadWrite.All
- (Mail.ReadWrite, Mail.Send, MailboxSettings.ReadWrite — grant only if you plan to use cross-mailbox ops; else skip to keep blast radius narrow.)

Add a "Re-consent after upgrade" note: users running 0.1.0 who upgrade to 0.2.0 must re-run `./bin/od-auth login` after granting admin consent for the new mail scopes, or delegated tokens will 403 on `/me/mailFolders/inbox`.

- [ ] **Step 2: Commit.**

```bash
git add docs/setup/azure-app-registration.md
git commit -m "docs(setup): update azure-app-registration with Mail.* delegated + application scopes"
```

---

## Acceptance gates

### Task 25: Full test + smoke

- [ ] **Step 1: Suite.**

```bash
uv run pytest -m "not live" -q 2>&1 | tail -3
```
Expected: ~310+ passed, 1 deselected (246 baseline + ~65 new across models/messages/folders/categories/rules/settings/attachments/CLI).

- [ ] **Step 2: Ruff + mypy.**

```bash
uv run ruff check 2>&1 | tail -5
uv run mypy src 2>&1 | tail -10
```
Ruff must be clean. Mypy's 0.1.0 baseline can grow by a few errors in the new mail tree but not explode — if new errors > 10, triage before landing.

- [ ] **Step 3: CLI smoke (no-auth, parse-only).**

```bash
uv run python -m m365ctl mail --help
uv run python -m m365ctl mail list --help
uv run python -m m365ctl mail get --help
uv run python -m m365ctl mail folders --help
uv run python -m m365ctl mail whoami --help
./bin/mail-list --help
```
All should return 0 and print arg banners.

- [ ] **Step 4: Live-tenant smoke (user-performed).**

Request the user run:

```bash
./bin/od-auth login                                 # after granting admin consent
./bin/mail-whoami                                   # identity + mail-access + cert + catalog stub
./bin/mail-folders --tree                           # full folder tree
./bin/mail-list --folder Inbox --unread --limit 10  # 10 unread in inbox
./bin/mail-get <id-from-list> --with-body           # full message view
./bin/mail-search "from:me" --limit 5
./bin/mail-categories
./bin/mail-rules list
./bin/mail-settings show
```

If any returns a 403, the remediation is admin consent at the URL printed by `mail-whoami`.

---

### Task 26: Branch merge

- [ ] **Step 1: Push + PR.**

```bash
git push -u origin phase-1-mail-readers
gh pr create --title "Phase 1: mail readers + auth scope expansion" --body "..."
```

- [ ] **Step 2: CI green → merge → delete branch.**

```bash
gh pr checks <N> --watch
gh pr merge <N> --merge --delete-branch
git checkout main && git pull
```

---

## Self-Review Checklist

**1. Spec coverage (spec §19 Phase 1 deliverables):**
- [x] `Mail.ReadWrite`, `Mail.Send`, `MailboxSettings.ReadWrite` → Task 1
- [x] `mail.endpoints.user_base` → Task 2
- [x] `mail.models` dataclasses → Task 3
- [x] `mail.messages.{list,get,search_graph,thread}` → Task 5
- [x] `mail.folders.{list,resolve_path,get}` → Task 6
- [x] `mail.categories.list_master_categories` → Task 7
- [x] `mail.rules.{list,get}_rule` → Task 8
- [x] `mail.settings.{get_settings,get_auto_reply}` → Task 9
- [x] `mail.attachments.{list,get}_attachment` → Task 10
- [x] `mail.cli.{list,get,search,folders,categories,rules,settings,attach}` → Tasks 13–20
- [x] `bin/mail-*` wrappers → Task 21
- [x] `bin/mail-auth`, `bin/mail-whoami` → Tasks 12 + 21
- [x] `common.safety.assert_mailbox_allowed` → Task 4
- [x] Scope-violation test → Task 22
- [x] Version bump 0.2.0 + CHANGELOG → Task 23
- [x] Entra docs update → Task 24

**2. Acceptance (spec §19 Phase 1):**
- `mail-folders --tree` → exercised in Task 16 + Task 25 Step 4.
- `mail-list --folder Inbox --unread --limit 10 --json` → Task 13 + Task 25 Step 4.
- `mail-get <id> --with-body --with-attachments --json` → Task 14 + Task 25 Step 4.
- `mail-search "from:alice AND subject:meeting"` → Task 15 + Task 25 Step 4.
- `mail-categories`, `mail-rules`, `mail-settings` → Tasks 17/18/19 + Task 25 Step 4.
- `mail-whoami` surfaces missing scope with remediation URL → Task 12 `run_whoami`.
- Scope-violation → Task 22.

**3. Placeholder scan:** No TODOs, no "implement later" sprinkled through. `mail get --eml` and `mail search --local` are explicit deferrals documented in the code + spec phase references.

**4. Type consistency:**
- `user_base(spec, *, auth_mode=…)` — keyword consistent across Tasks 2, 5, 6, 7, 8, 9, 10.
- `Message.from_graph_json(raw, *, mailbox_upn, parent_folder_path)` — used consistently in messages.py, list.py, get.py, search.py.
- `assert_mailbox_allowed(spec, cfg, *, auth_mode, unsafe_scope)` — consistent signature across safety.py, _common.py.
- `list_messages(graph, *, mailbox_spec, auth_mode, folder_id, parent_folder_path, filters=None, limit=None, page_size=50)` — matches its test fixtures exactly.

---

Plan complete and saved to `docs/superpowers/plans/2026-04-24-phase-1-mail-readers.md`.
