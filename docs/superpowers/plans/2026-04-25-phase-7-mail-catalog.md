# Phase 7 — Local Mail Catalog (DuckDB + `/delta` sync) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan group-by-group. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mirror mailbox metadata (folders, messages, categories) into a DuckDB catalog at `[mail].catalog_path` (default `cache/mail.duckdb`) via Graph `/delta`, so `mail search --local`, `mail whoami` stats, and (later) Phase 10 triage DSL can run offline. First call full-syncs; subsequent calls are incremental; `syncStateNotFound` → clean restart.

**Architecture:** New `m365ctl.mail.catalog` package with four siblings: `schema` (DDL + version), `db` (connection ctx), `crawl` (folder enumeration + per-folder delta with restart semantics), `queries` (six canonical SQL helpers). Reuses `GraphClient.get_paginated` (already returns `(items, delta_link)` per page). New CLI subcommand `mail catalog {refresh,status}` + bin wrappers `mail-catalog-refresh` / `mail-catalog-status`. `mail search --local` runs DuckDB `LIKE` against `mail_messages`; `mail whoami` reads catalog summary instead of the placeholder line. The catalog is **separate** from the OneDrive catalog (different DuckDB file, no cross-domain joins).

**Tech Stack:** Python 3.11+, DuckDB (already a dep), MSAL (existing `DelegatedCredential` / `AppOnlyCredential`), Microsoft Graph `/me/mailFolders/{id}/messages/delta`, pytest + MagicMock for unit tests, `httpx.MockTransport` for end-to-end CLI tests.

**Baseline:** `main` at `d0489c6` (Phase 5a merged), 544 passing tests, 75 mypy errors (pre-existing, non-blocking).

**Version bump:** 0.6.0 → 0.7.0.

---

## File Structure

**New:**
- `src/m365ctl/mail/catalog/schema.py` — DDL for `mail_messages`, `mail_folders`, `mail_categories`, `mail_deltas`, `mail_schema_meta`; `apply_schema(conn)` migration helper.
- `src/m365ctl/mail/catalog/db.py` — `open_catalog(path)` context manager (mkdir + connect + apply_schema + close).
- `src/m365ctl/mail/catalog/normalize.py` — `normalize_message(mailbox_upn, raw, parent_folder_path)` and `normalize_folder(mailbox_upn, raw, path)` → flat dicts ready for upsert.
- `src/m365ctl/mail/catalog/crawl.py` — `enumerate_target_folders`, `crawl_folder` (delta resume + 410 restart), `refresh_mailbox` (high-level orchestrator).
- `src/m365ctl/mail/catalog/queries.py` — `unread_in_folder`, `older_than`, `by_sender`, `attachments_by_size`, `top_senders`, `size_per_folder`, plus a `summary(conn)` helper for whoami.
- `src/m365ctl/mail/cli/catalog.py` — `mail catalog {refresh,status}` argparse + handlers.
- `bin/mail-catalog-refresh`, `bin/mail-catalog-status` — exec wrappers (mirror `bin/od-catalog-*`).
- `tests/test_mail_catalog_schema.py`
- `tests/test_mail_catalog_db.py`
- `tests/test_mail_catalog_normalize.py`
- `tests/test_mail_catalog_crawl.py` — mocks Graph `get_paginated` for happy path + 410 restart.
- `tests/test_mail_catalog_queries.py`
- `tests/test_cli_mail_catalog.py`
- `tests/test_cli_mail_search_local.py` — fills in the `--local` end-to-end behaviour.

**Modify:**
- `src/m365ctl/mail/cli/__main__.py` — route new verb `catalog`.
- `src/m365ctl/mail/cli/search.py` — replace the `--local: arrives in Phase 7` stub with a real DuckDB query path; add `--hybrid` (default) flag semantics.
- `src/m365ctl/mail/cli/whoami.py` — replace `Mail catalog: (not yet built — Phase 7)` with real summary numbers.
- `pyproject.toml` — bump `0.6.0` → `0.7.0`.
- `CHANGELOG.md` — `0.7.0` entry.
- `README.md` — short bullet under "Mail" naming `mail catalog refresh`/`status` + `mail search --local`.

---

## Group 1 — Schema, DB connection, normalisers

**Files:**
- Create: `src/m365ctl/mail/catalog/schema.py`
- Create: `src/m365ctl/mail/catalog/db.py`
- Create: `src/m365ctl/mail/catalog/normalize.py`
- Create: `tests/test_mail_catalog_schema.py`
- Create: `tests/test_mail_catalog_db.py`
- Create: `tests/test_mail_catalog_normalize.py`

The schema mirrors the OneDrive precedent (`src/m365ctl/onedrive/catalog/schema.py`) but for mail entities. Composite primary keys are `(mailbox_upn, <id>)` — when delegation lands in Phase 12 we will already key by mailbox, so multi-mailbox catalogs work without migration.

### Task 1.1: Schema module + tests

- [ ] **Step 1: Write the failing tests** (`tests/test_mail_catalog_schema.py`)

```python
from __future__ import annotations

import duckdb

from m365ctl.mail.catalog.schema import CURRENT_SCHEMA_VERSION, apply_schema


def test_apply_schema_creates_all_tables() -> None:
    conn = duckdb.connect(":memory:")
    apply_schema(conn)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }
    assert {
        "mail_schema_meta",
        "mail_folders",
        "mail_messages",
        "mail_categories",
        "mail_deltas",
    } <= tables


def test_apply_schema_records_version_once() -> None:
    conn = duckdb.connect(":memory:")
    apply_schema(conn)
    apply_schema(conn)  # idempotent
    (count,) = conn.execute(
        "SELECT COUNT(*) FROM mail_schema_meta WHERE version = ?",
        [CURRENT_SCHEMA_VERSION],
    ).fetchone()
    assert count == 1


def test_messages_pk_is_composite() -> None:
    conn = duckdb.connect(":memory:")
    apply_schema(conn)
    # Insert two rows that differ only by mailbox_upn — both should succeed.
    for upn in ("a@example.com", "b@example.com"):
        conn.execute(
            "INSERT INTO mail_messages (mailbox_upn, message_id, parent_folder_id, "
            "subject, received_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
            [upn, "msg-1", "fld-1", "x", "2026-01-01", "2026-01-01"],
        )
    (n,) = conn.execute("SELECT COUNT(*) FROM mail_messages").fetchone()
    assert n == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mail_catalog_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'm365ctl.mail.catalog.schema'`.

- [ ] **Step 3: Implement schema module** (`src/m365ctl/mail/catalog/schema.py`)

```python
"""DuckDB schema for the mail catalog.

One-shot migration: ``apply_schema(conn)`` creates the tables if missing
and records the version in ``mail_schema_meta``. Future plans bump
``CURRENT_SCHEMA_VERSION`` and add branches.

Composite PKs always lead with ``mailbox_upn`` so the catalog can hold
multiple mailboxes side-by-side once Phase 12 (delegation) lands.
"""
from __future__ import annotations

import duckdb

CURRENT_SCHEMA_VERSION = 1

_DDL_V1 = """
CREATE TABLE IF NOT EXISTS mail_schema_meta (
    version INTEGER NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mail_folders (
    mailbox_upn        VARCHAR NOT NULL,
    folder_id          VARCHAR NOT NULL,
    display_name       VARCHAR,
    parent_folder_id   VARCHAR,
    path               VARCHAR,
    well_known_name    VARCHAR,
    total_items        INTEGER,
    unread_items       INTEGER,
    child_folder_count INTEGER,
    last_seen_at       TIMESTAMP,
    PRIMARY KEY (mailbox_upn, folder_id)
);

CREATE TABLE IF NOT EXISTS mail_messages (
    mailbox_upn          VARCHAR NOT NULL,
    message_id           VARCHAR NOT NULL,
    internet_message_id  VARCHAR,
    conversation_id      VARCHAR,
    parent_folder_id     VARCHAR NOT NULL,
    parent_folder_path   VARCHAR,
    subject              VARCHAR,
    from_address         VARCHAR,
    from_name            VARCHAR,
    to_addresses         VARCHAR,   -- comma-joined for cheap LIKE search
    received_at          TIMESTAMP,
    sent_at              TIMESTAMP,
    is_read              BOOLEAN,
    is_draft             BOOLEAN,
    has_attachments      BOOLEAN,
    importance           VARCHAR,
    flag_status          VARCHAR,
    categories           VARCHAR,   -- comma-joined
    inference_class      VARCHAR,
    body_preview         VARCHAR,
    web_link             VARCHAR,
    size_estimate        BIGINT,    -- bodyPreview + attachment sum approx
    is_deleted           BOOLEAN NOT NULL DEFAULT FALSE,
    last_seen_at         TIMESTAMP,
    PRIMARY KEY (mailbox_upn, message_id)
);

CREATE INDEX IF NOT EXISTS idx_mail_messages_received
    ON mail_messages(mailbox_upn, received_at);
CREATE INDEX IF NOT EXISTS idx_mail_messages_from
    ON mail_messages(mailbox_upn, from_address);
CREATE INDEX IF NOT EXISTS idx_mail_messages_folder
    ON mail_messages(mailbox_upn, parent_folder_id);
CREATE INDEX IF NOT EXISTS idx_mail_messages_unread
    ON mail_messages(mailbox_upn, is_read);

CREATE TABLE IF NOT EXISTS mail_categories (
    mailbox_upn  VARCHAR NOT NULL,
    category_id  VARCHAR NOT NULL,
    display_name VARCHAR,
    color        VARCHAR,
    last_seen_at TIMESTAMP,
    PRIMARY KEY (mailbox_upn, category_id)
);

CREATE TABLE IF NOT EXISTS mail_deltas (
    mailbox_upn        VARCHAR NOT NULL,
    folder_id          VARCHAR NOT NULL,
    delta_link         VARCHAR,
    last_refreshed_at  TIMESTAMP,
    last_status        VARCHAR,    -- 'ok' | 'restarted' | 'failed'
    PRIMARY KEY (mailbox_upn, folder_id)
);
"""


def apply_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(_DDL_V1)
    (already,) = conn.execute(
        "SELECT COUNT(*) FROM mail_schema_meta WHERE version = ?",
        [CURRENT_SCHEMA_VERSION],
    ).fetchone()
    if already == 0:
        conn.execute(
            "INSERT INTO mail_schema_meta (version) VALUES (?)",
            [CURRENT_SCHEMA_VERSION],
        )
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_mail_catalog_schema.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/mail/catalog/schema.py tests/test_mail_catalog_schema.py
git commit -m "feat(mail/catalog): DuckDB schema for mail_messages/folders/categories/deltas"
```

### Task 1.2: DB connection helper

- [ ] **Step 1: Write the failing test** (`tests/test_mail_catalog_db.py`)

```python
from __future__ import annotations

from pathlib import Path

from m365ctl.mail.catalog.db import open_catalog


def test_open_catalog_creates_parent_dirs(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "mail.duckdb"
    with open_catalog(db_path) as conn:
        (n,) = conn.execute("SELECT COUNT(*) FROM mail_messages").fetchone()
    assert n == 0
    assert db_path.exists()


def test_open_catalog_persists_across_opens(tmp_path: Path) -> None:
    db_path = tmp_path / "mail.duckdb"
    with open_catalog(db_path) as conn:
        conn.execute(
            "INSERT INTO mail_folders (mailbox_upn, folder_id, display_name, "
            "last_seen_at) VALUES (?, ?, ?, ?)",
            ["me", "fld-1", "Inbox", "2026-01-01"],
        )
    with open_catalog(db_path) as conn:
        (n,) = conn.execute("SELECT COUNT(*) FROM mail_folders").fetchone()
    assert n == 1
```

- [ ] **Step 2: Run test, verify fail** — Run: `uv run pytest tests/test_mail_catalog_db.py -v`. Expected: ImportError.

- [ ] **Step 3: Implement db helper** (`src/m365ctl/mail/catalog/db.py`)

```python
"""DuckDB connection helper for the mail catalog."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from m365ctl.mail.catalog.schema import apply_schema


@contextmanager
def open_catalog(path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))
    try:
        apply_schema(conn)
        yield conn
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests, verify pass.** Run: `uv run pytest tests/test_mail_catalog_db.py -v`. Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/mail/catalog/db.py tests/test_mail_catalog_db.py
git commit -m "feat(mail/catalog): open_catalog context manager (mkdir + apply_schema)"
```

### Task 1.3: Normalisers (Graph JSON → row dict)

- [ ] **Step 1: Write the failing tests** (`tests/test_mail_catalog_normalize.py`)

```python
from __future__ import annotations

from datetime import datetime, timezone

from m365ctl.mail.catalog.normalize import normalize_folder, normalize_message


def test_normalize_message_full_payload() -> None:
    raw = {
        "id": "msg-1",
        "internetMessageId": "<abc@example.com>",
        "conversationId": "conv-1",
        "parentFolderId": "fld-inbox",
        "subject": "Hello",
        "from": {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
        "toRecipients": [
            {"emailAddress": {"name": "Bob", "address": "bob@example.com"}},
            {"emailAddress": {"name": "Carol", "address": "carol@example.com"}},
        ],
        "receivedDateTime": "2026-04-01T10:00:00Z",
        "sentDateTime": "2026-04-01T09:59:59Z",
        "isRead": False,
        "isDraft": False,
        "hasAttachments": True,
        "importance": "high",
        "flag": {"flagStatus": "flagged"},
        "categories": ["Work", "Urgent"],
        "inferenceClassification": "focused",
        "bodyPreview": "preview text",
        "webLink": "https://outlook.office.com/...",
    }
    row = normalize_message("me", raw, parent_folder_path="Inbox")
    assert row["mailbox_upn"] == "me"
    assert row["message_id"] == "msg-1"
    assert row["from_address"] == "alice@example.com"
    assert row["from_name"] == "Alice"
    assert row["to_addresses"] == "bob@example.com,carol@example.com"
    assert row["categories"] == "Work,Urgent"
    assert row["is_read"] is False
    assert row["has_attachments"] is True
    assert row["parent_folder_path"] == "Inbox"
    assert row["is_deleted"] is False
    assert isinstance(row["last_seen_at"], datetime)


def test_normalize_message_deleted_tombstone() -> None:
    """Graph delta returns ``{"id": "...", "@removed": {"reason": "deleted"}}``."""
    raw = {"id": "msg-2", "@removed": {"reason": "deleted"}}
    row = normalize_message("me", raw, parent_folder_path="")
    assert row["message_id"] == "msg-2"
    assert row["is_deleted"] is True
    # Tombstones have minimal data; everything else is None / defaults.
    assert row["subject"] is None
    assert row["received_at"] is None


def test_normalize_message_handles_missing_from() -> None:
    raw = {
        "id": "msg-3",
        "parentFolderId": "fld-drafts",
        "subject": "Draft",
        "receivedDateTime": "2026-04-01T10:00:00Z",
    }
    row = normalize_message("me", raw, parent_folder_path="Drafts")
    assert row["from_address"] is None
    assert row["from_name"] is None
    assert row["to_addresses"] == ""


def test_normalize_folder() -> None:
    raw = {
        "id": "fld-inbox",
        "displayName": "Inbox",
        "parentFolderId": "fld-root",
        "wellKnownName": "inbox",
        "totalItemCount": 100,
        "unreadItemCount": 7,
        "childFolderCount": 2,
    }
    row = normalize_folder("me", raw, path="Inbox")
    assert row["folder_id"] == "fld-inbox"
    assert row["display_name"] == "Inbox"
    assert row["unread_items"] == 7
    assert row["well_known_name"] == "inbox"
```

- [ ] **Step 2: Run tests, verify fail.**

- [ ] **Step 3: Implement** (`src/m365ctl/mail/catalog/normalize.py`)

```python
"""Normalize Graph mail JSON into flat dicts ready for DuckDB upsert.

Two shapes from ``/messages/delta``:
- Full message: standard payload.
- Deleted tombstone: ``{"id": "...", "@removed": {"reason": "deleted"}}``
  (Graph also uses a top-level ``@removed`` key for soft tombstones).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from m365ctl.mail.models import _parse_graph_datetime  # type: ignore[attr-defined]


def _addr(block: dict | None) -> tuple[str | None, str | None]:
    if not block:
        return None, None
    inner = block.get("emailAddress") or {}
    return inner.get("name") or None, inner.get("address") or None


def _join_addrs(items: list[dict] | None) -> str:
    if not items:
        return ""
    out: list[str] = []
    for it in items:
        inner = (it or {}).get("emailAddress") or {}
        addr = inner.get("address")
        if addr:
            out.append(addr)
    return ",".join(out)


def normalize_message(
    mailbox_upn: str, raw: dict, *, parent_folder_path: str
) -> dict[str, Any]:
    is_deleted = bool(raw.get("@removed"))
    if is_deleted:
        return {
            "mailbox_upn": mailbox_upn,
            "message_id": raw["id"],
            "internet_message_id": None,
            "conversation_id": None,
            "parent_folder_id": "",
            "parent_folder_path": "",
            "subject": None,
            "from_address": None,
            "from_name": None,
            "to_addresses": "",
            "received_at": None,
            "sent_at": None,
            "is_read": None,
            "is_draft": None,
            "has_attachments": None,
            "importance": None,
            "flag_status": None,
            "categories": "",
            "inference_class": None,
            "body_preview": None,
            "web_link": None,
            "size_estimate": None,
            "is_deleted": True,
            "last_seen_at": datetime.now(timezone.utc),
        }

    from_name, from_addr = _addr(raw.get("from"))
    flag = raw.get("flag") or {}
    return {
        "mailbox_upn": mailbox_upn,
        "message_id": raw["id"],
        "internet_message_id": raw.get("internetMessageId"),
        "conversation_id": raw.get("conversationId"),
        "parent_folder_id": raw.get("parentFolderId", ""),
        "parent_folder_path": parent_folder_path,
        "subject": raw.get("subject"),
        "from_address": from_addr,
        "from_name": from_name,
        "to_addresses": _join_addrs(raw.get("toRecipients")),
        "received_at": _parse_graph_datetime(raw.get("receivedDateTime")),
        "sent_at": _parse_graph_datetime(raw.get("sentDateTime")),
        "is_read": raw.get("isRead"),
        "is_draft": raw.get("isDraft"),
        "has_attachments": raw.get("hasAttachments"),
        "importance": raw.get("importance"),
        "flag_status": flag.get("flagStatus"),
        "categories": ",".join(raw.get("categories") or []),
        "inference_class": raw.get("inferenceClassification"),
        "body_preview": raw.get("bodyPreview"),
        "web_link": raw.get("webLink"),
        "size_estimate": None,
        "is_deleted": False,
        "last_seen_at": datetime.now(timezone.utc),
    }


def normalize_folder(
    mailbox_upn: str, raw: dict, *, path: str
) -> dict[str, Any]:
    return {
        "mailbox_upn": mailbox_upn,
        "folder_id": raw["id"],
        "display_name": raw.get("displayName"),
        "parent_folder_id": raw.get("parentFolderId"),
        "path": path,
        "well_known_name": raw.get("wellKnownName"),
        "total_items": raw.get("totalItemCount"),
        "unread_items": raw.get("unreadItemCount"),
        "child_folder_count": raw.get("childFolderCount"),
        "last_seen_at": datetime.now(timezone.utc),
    }
```

- [ ] **Step 4: Run tests, verify pass.** Run: `uv run pytest tests/test_mail_catalog_normalize.py -v`. Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/mail/catalog/normalize.py tests/test_mail_catalog_normalize.py
git commit -m "feat(mail/catalog): normalize Graph message/folder JSON into row dicts (incl. tombstones)"
```

---

## Group 2 — Crawl orchestrator (per-folder delta + 410 restart)

**Files:**
- Create: `src/m365ctl/mail/catalog/crawl.py`
- Create: `tests/test_mail_catalog_crawl.py`

### Task 2.1: Folder enumerator + per-folder delta + restart on syncStateNotFound

**Design notes:**
- Default target folders for `refresh_mailbox`: well-known `inbox`, `sentitems`, `drafts`, `archive` (if present), `deleteditems`. Caller can pass a single `folder_id` to refresh just that one (CLI `--folder Inbox/Triage` resolves to id then calls `crawl_folder`).
- `crawl_folder` reads the prior `delta_link` from `mail_deltas`. If absent, starts at `/{ub}/mailFolders/{id}/messages/delta?$top=200`.
- The Graph 410 `syncStateNotFound` (delta token expiry) is recognised by `GraphError` text containing `syncStateNotFound`. On detection: clear the row's `delta_link`, log a `[catalog] delta restart` line to stderr, restart from initial path. Mark `last_status='restarted'` after the rebuild succeeds.
- Folders are upserted from `list_folders` (already lazy + recursive in `mail/folders.py`) before message crawl, so `parent_folder_path` joins work in queries.
- All upserts use `INSERT ... ON CONFLICT ... DO UPDATE SET ...` (DuckDB supports this).

- [ ] **Step 1: Write the failing tests** (`tests/test_mail_catalog_crawl.py`)

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from m365ctl.common.graph import GraphError
from m365ctl.mail.catalog.crawl import (
    CrawlOutcome,
    crawl_folder,
    refresh_mailbox,
)
from m365ctl.mail.catalog.db import open_catalog


def _msg(mid: str, *, folder: str = "fld-inbox", subject: str = "x") -> dict:
    return {
        "id": mid,
        "parentFolderId": folder,
        "subject": subject,
        "receivedDateTime": "2026-04-01T10:00:00Z",
        "from": {"emailAddress": {"name": "A", "address": "a@example.com"}},
        "isRead": False,
    }


def test_crawl_folder_first_run_full_sync(tmp_path: Path) -> None:
    graph = MagicMock()
    graph.get_paginated.return_value = iter(
        [
            ([_msg("m1"), _msg("m2")], None),
            ([_msg("m3")], "https://graph.microsoft.com/.../delta?token=DELTA1"),
        ]
    )
    with open_catalog(tmp_path / "m.duckdb") as conn:
        outcome = crawl_folder(
            graph,
            conn=conn,
            mailbox_upn="me",
            folder_id="fld-inbox",
            folder_path="Inbox",
            initial_path="/me/mailFolders/fld-inbox/messages/delta",
            page_top=200,
        )
        assert outcome.messages_seen == 3
        assert outcome.delta_link.endswith("DELTA1")
        assert outcome.status == "ok"
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM mail_messages WHERE mailbox_upn = 'me'"
        ).fetchone()
        assert n == 3
        (link,) = conn.execute(
            "SELECT delta_link FROM mail_deltas "
            "WHERE mailbox_upn = 'me' AND folder_id = 'fld-inbox'"
        ).fetchone()
        assert link.endswith("DELTA1")


def test_crawl_folder_resumes_from_stored_delta_link(tmp_path: Path) -> None:
    graph = MagicMock()
    graph.get_paginated.return_value = iter(
        [([_msg("m4")], "https://graph.microsoft.com/.../delta?token=DELTA2")]
    )
    with open_catalog(tmp_path / "m.duckdb") as conn:
        conn.execute(
            "INSERT INTO mail_deltas (mailbox_upn, folder_id, delta_link, "
            "last_refreshed_at, last_status) VALUES (?, ?, ?, ?, ?)",
            ["me", "fld-inbox", "https://stored/delta-prior", "2026-04-01", "ok"],
        )
        crawl_folder(
            graph,
            conn=conn,
            mailbox_upn="me",
            folder_id="fld-inbox",
            folder_path="Inbox",
            initial_path="/me/mailFolders/fld-inbox/messages/delta",
            page_top=200,
        )
    # The stored delta_link should have been used as the starting path.
    args = graph.get_paginated.call_args
    assert args is not None
    (called_path, *_), _kw = args.args, args.kwargs
    assert called_path == "https://stored/delta-prior"


def test_crawl_folder_410_sync_state_not_found_restarts(tmp_path: Path) -> None:
    graph = MagicMock()
    # First call raises, second call (after we drop delta_link) succeeds.
    graph.get_paginated.side_effect = [
        _raises_sync_state_not_found(),
        iter([([_msg("m5")], "https://graph.microsoft.com/.../delta?token=FRESH")]),
    ]
    with open_catalog(tmp_path / "m.duckdb") as conn:
        conn.execute(
            "INSERT INTO mail_deltas (mailbox_upn, folder_id, delta_link, "
            "last_refreshed_at, last_status) VALUES (?, ?, ?, ?, ?)",
            ["me", "fld-inbox", "https://stored/delta-expired", "2026-04-01", "ok"],
        )
        outcome = crawl_folder(
            graph,
            conn=conn,
            mailbox_upn="me",
            folder_id="fld-inbox",
            folder_path="Inbox",
            initial_path="/me/mailFolders/fld-inbox/messages/delta",
            page_top=200,
        )
        assert outcome.status == "restarted"
        assert outcome.messages_seen == 1
        (status,) = conn.execute(
            "SELECT last_status FROM mail_deltas "
            "WHERE mailbox_upn = 'me' AND folder_id = 'fld-inbox'"
        ).fetchone()
        assert status == "restarted"


def _raises_sync_state_not_found():
    def _gen():
        raise GraphError("HTTP410 syncStateNotFound: token expired", status=410)
        yield  # pragma: no cover
    return _gen()


def test_refresh_mailbox_picks_default_well_known_folders(tmp_path: Path) -> None:
    """refresh_mailbox enumerates Inbox/Sent/Drafts/DeletedItems by well-known name."""
    graph = MagicMock()
    # mail_folders root listing → 4 well-known folders.
    folders = [
        {
            "id": f"fld-{wk}",
            "displayName": wk.title(),
            "wellKnownName": wk,
            "childFolderCount": 0,
            "totalItemCount": 0,
            "unreadItemCount": 0,
        }
        for wk in ("inbox", "sentitems", "drafts", "deleteditems")
    ]
    graph.get_paginated.side_effect = (
        # First call: list_folders top-level.
        [iter([(folders, None)])]
        # Then four delta crawls, each one page, no items.
        + [iter([([], f"delta-{wk}")]) for wk in
           ("inbox", "sentitems", "drafts", "deleteditems")]
    )
    with open_catalog(tmp_path / "m.duckdb") as conn:
        outcomes = refresh_mailbox(
            graph,
            conn=conn,
            mailbox_spec="me",
            mailbox_upn="me",
            auth_mode="delegated",
        )
    assert {o.folder_id for o in outcomes} == {
        "fld-inbox", "fld-sentitems", "fld-drafts", "fld-deleteditems",
    }
    assert all(o.status == "ok" for o in outcomes)
    (n,) = conn.execute("SELECT COUNT(*) FROM mail_folders").fetchone()
    assert n == 4
```

- [ ] **Step 2: Run tests, verify fail** (ImportError / undefined symbols).

- [ ] **Step 3: Implement crawl module** (`src/m365ctl/mail/catalog/crawl.py`)

```python
"""Per-folder ``/messages/delta`` crawler for the mail catalog.

Resumes from stored ``delta_link`` when present. On Graph
``syncStateNotFound`` (HTTP 410), drops the stored link and full-resyncs
the folder, marking ``last_status='restarted'``.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from m365ctl.common.config import AuthMode
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.mail.catalog.normalize import normalize_folder, normalize_message
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.folders import list_folders


_DEFAULT_WELL_KNOWN: tuple[str, ...] = (
    "inbox", "sentitems", "drafts", "deleteditems",
)


@dataclass(frozen=True)
class CrawlOutcome:
    folder_id: str
    folder_path: str
    messages_seen: int
    delta_link: str | None
    status: str   # 'ok' | 'restarted'


_UPSERT_FOLDER = """
INSERT INTO mail_folders (
    mailbox_upn, folder_id, display_name, parent_folder_id, path,
    well_known_name, total_items, unread_items, child_folder_count, last_seen_at
) VALUES (
    $mailbox_upn, $folder_id, $display_name, $parent_folder_id, $path,
    $well_known_name, $total_items, $unread_items, $child_folder_count, $last_seen_at
)
ON CONFLICT (mailbox_upn, folder_id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    parent_folder_id = EXCLUDED.parent_folder_id,
    path = EXCLUDED.path,
    well_known_name = EXCLUDED.well_known_name,
    total_items = EXCLUDED.total_items,
    unread_items = EXCLUDED.unread_items,
    child_folder_count = EXCLUDED.child_folder_count,
    last_seen_at = EXCLUDED.last_seen_at
"""

_UPSERT_MESSAGE = """
INSERT INTO mail_messages (
    mailbox_upn, message_id, internet_message_id, conversation_id,
    parent_folder_id, parent_folder_path, subject, from_address, from_name,
    to_addresses, received_at, sent_at, is_read, is_draft, has_attachments,
    importance, flag_status, categories, inference_class, body_preview,
    web_link, size_estimate, is_deleted, last_seen_at
) VALUES (
    $mailbox_upn, $message_id, $internet_message_id, $conversation_id,
    $parent_folder_id, $parent_folder_path, $subject, $from_address, $from_name,
    $to_addresses, $received_at, $sent_at, $is_read, $is_draft, $has_attachments,
    $importance, $flag_status, $categories, $inference_class, $body_preview,
    $web_link, $size_estimate, $is_deleted, $last_seen_at
)
ON CONFLICT (mailbox_upn, message_id) DO UPDATE SET
    internet_message_id = EXCLUDED.internet_message_id,
    conversation_id = EXCLUDED.conversation_id,
    parent_folder_id = EXCLUDED.parent_folder_id,
    parent_folder_path = EXCLUDED.parent_folder_path,
    subject = EXCLUDED.subject,
    from_address = EXCLUDED.from_address,
    from_name = EXCLUDED.from_name,
    to_addresses = EXCLUDED.to_addresses,
    received_at = EXCLUDED.received_at,
    sent_at = EXCLUDED.sent_at,
    is_read = EXCLUDED.is_read,
    is_draft = EXCLUDED.is_draft,
    has_attachments = EXCLUDED.has_attachments,
    importance = EXCLUDED.importance,
    flag_status = EXCLUDED.flag_status,
    categories = EXCLUDED.categories,
    inference_class = EXCLUDED.inference_class,
    body_preview = EXCLUDED.body_preview,
    web_link = EXCLUDED.web_link,
    size_estimate = EXCLUDED.size_estimate,
    is_deleted = EXCLUDED.is_deleted,
    last_seen_at = EXCLUDED.last_seen_at
"""

_UPSERT_DELTA = """
INSERT INTO mail_deltas (
    mailbox_upn, folder_id, delta_link, last_refreshed_at, last_status
) VALUES (?, ?, ?, ?, ?)
ON CONFLICT (mailbox_upn, folder_id) DO UPDATE SET
    delta_link = EXCLUDED.delta_link,
    last_refreshed_at = EXCLUDED.last_refreshed_at,
    last_status = EXCLUDED.last_status
"""


def _is_sync_state_not_found(exc: GraphError) -> bool:
    msg = str(exc).lower()
    return "syncstatenotfound" in msg or "410" in msg


def _stored_delta_link(conn, *, mailbox_upn: str, folder_id: str) -> str | None:
    row = conn.execute(
        "SELECT delta_link FROM mail_deltas "
        "WHERE mailbox_upn = ? AND folder_id = ?",
        [mailbox_upn, folder_id],
    ).fetchone()
    return row[0] if row else None


def _drain_delta(
    graph: GraphClient,
    conn,
    *,
    mailbox_upn: str,
    folder_id: str,
    folder_path: str,
    start_path: str,
    page_top: int,
) -> tuple[int, str | None]:
    seen = 0
    final_delta: str | None = None
    # Only the first request carries $top; nextLinks already encode it.
    if start_path.startswith("http"):
        pages = graph.get_paginated(start_path)
    else:
        pages = graph.get_paginated(start_path, params={"$top": page_top})
    for items, delta_link in pages:
        for raw in items:
            row = normalize_message(mailbox_upn, raw, parent_folder_path=folder_path)
            row.setdefault("parent_folder_id", folder_id)
            if not row.get("parent_folder_id"):
                row["parent_folder_id"] = folder_id
            conn.execute(_UPSERT_MESSAGE, row)
            seen += 1
        if delta_link:
            final_delta = delta_link
    return seen, final_delta


def crawl_folder(
    graph: GraphClient,
    *,
    conn,
    mailbox_upn: str,
    folder_id: str,
    folder_path: str,
    initial_path: str,
    page_top: int = 200,
) -> CrawlOutcome:
    stored = _stored_delta_link(conn, mailbox_upn=mailbox_upn, folder_id=folder_id)
    start_path = stored or initial_path
    status = "ok"
    try:
        seen, delta_link = _drain_delta(
            graph, conn,
            mailbox_upn=mailbox_upn, folder_id=folder_id,
            folder_path=folder_path, start_path=start_path, page_top=page_top,
        )
    except GraphError as exc:
        if not _is_sync_state_not_found(exc):
            raise
        print(
            f"[mail-catalog] delta token expired for {folder_path!r}; "
            f"restarting full sync...",
            file=sys.stderr,
        )
        status = "restarted"
        seen, delta_link = _drain_delta(
            graph, conn,
            mailbox_upn=mailbox_upn, folder_id=folder_id,
            folder_path=folder_path, start_path=initial_path, page_top=page_top,
        )

    final_link = delta_link or stored
    conn.execute(
        _UPSERT_DELTA,
        [mailbox_upn, folder_id, final_link, datetime.now(timezone.utc), status],
    )
    return CrawlOutcome(
        folder_id=folder_id,
        folder_path=folder_path,
        messages_seen=seen,
        delta_link=final_link,
        status=status,
    )


def refresh_mailbox(
    graph: GraphClient,
    *,
    conn,
    mailbox_spec: str,
    mailbox_upn: str,
    auth_mode: AuthMode,
    folder_filter: str | None = None,
    page_top: int = 200,
) -> list[CrawlOutcome]:
    """High-level orchestrator.

    1. List all folders (recursive) and upsert into ``mail_folders``.
    2. Pick targets: ``folder_filter`` (folder id) if given, else all
       well-known folders that exist in this mailbox.
    3. Crawl each target via ``crawl_folder``.
    """
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    seen_folders: list[dict] = []
    for folder in list_folders(
        graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
    ):
        row = normalize_folder(
            mailbox_upn,
            {
                "id": folder.id,
                "displayName": folder.display_name,
                "parentFolderId": folder.parent_id,
                "wellKnownName": folder.well_known_name,
                "totalItemCount": folder.total_items,
                "unreadItemCount": folder.unread_items,
                "childFolderCount": folder.child_folder_count,
            },
            path=folder.path,
        )
        conn.execute(_UPSERT_FOLDER, row)
        seen_folders.append({
            "id": folder.id,
            "path": folder.path,
            "well_known_name": folder.well_known_name,
        })

    if folder_filter is not None:
        targets = [f for f in seen_folders if f["id"] == folder_filter]
    else:
        targets = [
            f for f in seen_folders
            if (f["well_known_name"] or "").lower() in _DEFAULT_WELL_KNOWN
        ]

    outcomes: list[CrawlOutcome] = []
    for f in targets:
        outcomes.append(
            crawl_folder(
                graph,
                conn=conn,
                mailbox_upn=mailbox_upn,
                folder_id=f["id"],
                folder_path=f["path"],
                initial_path=f"{ub}/mailFolders/{f['id']}/messages/delta",
                page_top=page_top,
            )
        )
    return outcomes
```

- [ ] **Step 4: Run crawl tests** — Run: `uv run pytest tests/test_mail_catalog_crawl.py -v`. Expected: PASS, 4 tests.

- [ ] **Step 5: Run full mail test suite to confirm no regressions** — Run: `uv run pytest tests/ -k "mail" -q`. Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/m365ctl/mail/catalog/crawl.py tests/test_mail_catalog_crawl.py
git commit -m "feat(mail/catalog): per-folder /delta crawler with stored deltaLink resume + 410 restart"
```

---

## Group 3 — Canned queries

**Files:**
- Create: `src/m365ctl/mail/catalog/queries.py`
- Create: `tests/test_mail_catalog_queries.py`

Six required queries from spec §19 Phase 7. All return `list[dict]`. All scope by `mailbox_upn` and exclude soft-deleted messages.

### Task 3.1: Implement queries with TDD

- [ ] **Step 1: Write the failing tests** (`tests/test_mail_catalog_queries.py`)

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.catalog.queries import (
    attachments_by_size,
    by_sender,
    older_than,
    size_per_folder,
    summary,
    top_senders,
    unread_in_folder,
)


def _seed(conn, **overrides) -> None:
    base = {
        "mailbox_upn": "me",
        "message_id": "m-x",
        "internet_message_id": None,
        "conversation_id": None,
        "parent_folder_id": "fld-inbox",
        "parent_folder_path": "Inbox",
        "subject": "subj",
        "from_address": "alice@example.com",
        "from_name": "Alice",
        "to_addresses": "",
        "received_at": datetime.now(timezone.utc) - timedelta(days=1),
        "sent_at": None,
        "is_read": False,
        "is_draft": False,
        "has_attachments": False,
        "importance": "normal",
        "flag_status": "notFlagged",
        "categories": "",
        "inference_class": "focused",
        "body_preview": "",
        "web_link": "",
        "size_estimate": 0,
        "is_deleted": False,
        "last_seen_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    conn.execute(
        "INSERT INTO mail_messages (mailbox_upn, message_id, internet_message_id, "
        "conversation_id, parent_folder_id, parent_folder_path, subject, "
        "from_address, from_name, to_addresses, received_at, sent_at, is_read, "
        "is_draft, has_attachments, importance, flag_status, categories, "
        "inference_class, body_preview, web_link, size_estimate, is_deleted, "
        "last_seen_at) VALUES ("
        "$mailbox_upn, $message_id, $internet_message_id, $conversation_id, "
        "$parent_folder_id, $parent_folder_path, $subject, $from_address, "
        "$from_name, $to_addresses, $received_at, $sent_at, $is_read, $is_draft, "
        "$has_attachments, $importance, $flag_status, $categories, "
        "$inference_class, $body_preview, $web_link, $size_estimate, "
        "$is_deleted, $last_seen_at)",
        base,
    )


def test_unread_in_folder(tmp_path: Path) -> None:
    with open_catalog(tmp_path / "m.duckdb") as conn:
        _seed(conn, message_id="m1", is_read=False)
        _seed(conn, message_id="m2", is_read=True)
        _seed(conn, message_id="m3", is_read=False, is_deleted=True)
        rows = unread_in_folder(conn, mailbox_upn="me", folder_id="fld-inbox")
    assert [r["message_id"] for r in rows] == ["m1"]


def test_older_than(tmp_path: Path) -> None:
    with open_catalog(tmp_path / "m.duckdb") as conn:
        _seed(conn, message_id="old", received_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
        _seed(conn, message_id="new", received_at=datetime(2026, 4, 1, tzinfo=timezone.utc))
        rows = older_than(conn, mailbox_upn="me", cutoff="2025-01-01")
    assert [r["message_id"] for r in rows] == ["old"]


def test_by_sender(tmp_path: Path) -> None:
    with open_catalog(tmp_path / "m.duckdb") as conn:
        _seed(conn, message_id="a", from_address="alice@example.com")
        _seed(conn, message_id="b", from_address="bob@example.com")
        rows = by_sender(conn, mailbox_upn="me", sender="alice@example.com")
    assert [r["message_id"] for r in rows] == ["a"]


def test_attachments_by_size(tmp_path: Path) -> None:
    with open_catalog(tmp_path / "m.duckdb") as conn:
        _seed(conn, message_id="big", has_attachments=True, size_estimate=5_000_000)
        _seed(conn, message_id="small", has_attachments=True, size_estimate=1_000)
        _seed(conn, message_id="none", has_attachments=False, size_estimate=0)
        rows = attachments_by_size(conn, mailbox_upn="me", min_bytes=10_000)
    assert [r["message_id"] for r in rows] == ["big"]


def test_top_senders(tmp_path: Path) -> None:
    with open_catalog(tmp_path / "m.duckdb") as conn:
        for i, addr in enumerate(["a@x.com", "a@x.com", "a@x.com", "b@x.com"]):
            _seed(conn, message_id=f"m{i}", from_address=addr)
        rows = top_senders(conn, mailbox_upn="me", limit=2)
    assert rows[0]["from_address"] == "a@x.com"
    assert rows[0]["count"] == 3
    assert rows[1]["from_address"] == "b@x.com"


def test_size_per_folder(tmp_path: Path) -> None:
    with open_catalog(tmp_path / "m.duckdb") as conn:
        _seed(conn, message_id="i1", parent_folder_path="Inbox", size_estimate=100)
        _seed(conn, message_id="i2", parent_folder_path="Inbox", size_estimate=200)
        _seed(conn, message_id="s1", parent_folder_path="Sent Items", size_estimate=50)
        rows = size_per_folder(conn, mailbox_upn="me")
    by_path = {r["parent_folder_path"]: r for r in rows}
    assert by_path["Inbox"]["total_size"] == 300
    assert by_path["Sent Items"]["total_size"] == 50


def test_summary(tmp_path: Path) -> None:
    with open_catalog(tmp_path / "m.duckdb") as conn:
        _seed(conn, message_id="m1")
        _seed(conn, message_id="m2", is_deleted=True)
        s = summary(conn, mailbox_upn="me")
    assert s["messages_total"] == 1
    assert s["messages_deleted"] == 1
    assert "last_refreshed_at" in s
```

- [ ] **Step 2: Run tests, verify fail.**

- [ ] **Step 3: Implement** (`src/m365ctl/mail/catalog/queries.py`)

```python
"""Canned queries over the mail catalog.

All queries scope by ``mailbox_upn`` and exclude soft-deleted rows
(``is_deleted = false``) by default. Results are plain dicts so callers
can emit JSON, TSV, or pretty-print.
"""
from __future__ import annotations

from typing import Any

import duckdb

_LIVE_WHERE = "mailbox_upn = ? AND is_deleted = false"


def _rows_as_dicts(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def unread_in_folder(
    conn: duckdb.DuckDBPyConnection, *, mailbox_upn: str, folder_id: str,
) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"""
        SELECT message_id, subject, from_address, received_at, body_preview
        FROM mail_messages
        WHERE {_LIVE_WHERE}
          AND parent_folder_id = ?
          AND is_read = false
        ORDER BY received_at DESC
        """,
        [mailbox_upn, folder_id],
    )
    return _rows_as_dicts(cur)


def older_than(
    conn: duckdb.DuckDBPyConnection, *, mailbox_upn: str, cutoff: str,
) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"""
        SELECT message_id, subject, from_address, received_at, parent_folder_path
        FROM mail_messages
        WHERE {_LIVE_WHERE}
          AND received_at < CAST(? AS TIMESTAMP)
        ORDER BY received_at ASC
        """,
        [mailbox_upn, cutoff],
    )
    return _rows_as_dicts(cur)


def by_sender(
    conn: duckdb.DuckDBPyConnection, *, mailbox_upn: str, sender: str,
) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"""
        SELECT message_id, subject, from_address, received_at, parent_folder_path
        FROM mail_messages
        WHERE {_LIVE_WHERE}
          AND from_address = ?
        ORDER BY received_at DESC
        """,
        [mailbox_upn, sender],
    )
    return _rows_as_dicts(cur)


def attachments_by_size(
    conn: duckdb.DuckDBPyConnection, *, mailbox_upn: str, min_bytes: int,
) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"""
        SELECT message_id, subject, from_address, received_at, size_estimate
        FROM mail_messages
        WHERE {_LIVE_WHERE}
          AND has_attachments = true
          AND size_estimate >= ?
        ORDER BY size_estimate DESC
        """,
        [mailbox_upn, min_bytes],
    )
    return _rows_as_dicts(cur)


def top_senders(
    conn: duckdb.DuckDBPyConnection, *, mailbox_upn: str, limit: int = 20,
) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"""
        SELECT from_address, COUNT(*)::BIGINT AS count
        FROM mail_messages
        WHERE {_LIVE_WHERE}
          AND from_address IS NOT NULL
        GROUP BY from_address
        ORDER BY count DESC, from_address ASC
        LIMIT ?
        """,
        [mailbox_upn, limit],
    )
    return _rows_as_dicts(cur)


def size_per_folder(
    conn: duckdb.DuckDBPyConnection, *, mailbox_upn: str,
) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"""
        SELECT parent_folder_path,
               COUNT(*)::BIGINT AS message_count,
               COALESCE(SUM(size_estimate), 0)::BIGINT AS total_size
        FROM mail_messages
        WHERE {_LIVE_WHERE}
        GROUP BY parent_folder_path
        ORDER BY total_size DESC
        """,
        [mailbox_upn],
    )
    return _rows_as_dicts(cur)


def summary(
    conn: duckdb.DuckDBPyConnection, *, mailbox_upn: str,
) -> dict[str, Any]:
    (alive,) = conn.execute(
        f"SELECT COUNT(*) FROM mail_messages WHERE {_LIVE_WHERE}",
        [mailbox_upn],
    ).fetchone()
    (deleted,) = conn.execute(
        "SELECT COUNT(*) FROM mail_messages "
        "WHERE mailbox_upn = ? AND is_deleted = true",
        [mailbox_upn],
    ).fetchone()
    (folders,) = conn.execute(
        "SELECT COUNT(*) FROM mail_folders WHERE mailbox_upn = ?",
        [mailbox_upn],
    ).fetchone()
    last_row = conn.execute(
        "SELECT MAX(last_refreshed_at) FROM mail_deltas WHERE mailbox_upn = ?",
        [mailbox_upn],
    ).fetchone()
    return {
        "messages_total": alive,
        "messages_deleted": deleted,
        "folders_total": folders,
        "last_refreshed_at": last_row[0] if last_row else None,
    }
```

- [ ] **Step 4: Run tests, verify pass.** Run: `uv run pytest tests/test_mail_catalog_queries.py -v`. Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/mail/catalog/queries.py tests/test_mail_catalog_queries.py
git commit -m "feat(mail/catalog): canned queries (unread/older/sender/attachments/top-senders/size + summary)"
```

---

## Group 4 — CLI: `mail catalog refresh` and `mail catalog status`

**Files:**
- Create: `src/m365ctl/mail/cli/catalog.py`
- Create: `bin/mail-catalog-refresh`
- Create: `bin/mail-catalog-status`
- Modify: `src/m365ctl/mail/cli/__main__.py` (route `catalog`)
- Create: `tests/test_cli_mail_catalog.py`

### Task 4.1: CLI module

- [ ] **Step 1: Write the failing tests** (`tests/test_cli_mail_catalog.py`)

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from m365ctl.mail.catalog.crawl import CrawlOutcome
from m365ctl.mail.cli import catalog as cli_catalog


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""
tenant_id = "tenant"
client_id = "client"
cert_path = "{tmp_path / 'c.pem'}"
cert_public = "{tmp_path / 'p.cer'}"
default_auth = "delegated"

[scope]
allow_drives = ["me"]
allow_mailboxes = ["me"]

[catalog]
path = "{tmp_path / 'cat.duckdb'}"

[mail]
catalog_path = "{tmp_path / 'mail.duckdb'}"

[logging]
ops_dir = "{tmp_path / 'logs'}"
"""
    )
    return cfg


def test_catalog_refresh_invokes_refresh_mailbox(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    fake_outcomes = [
        CrawlOutcome("fld-inbox", "Inbox", 5, "delta-1", "ok"),
        CrawlOutcome("fld-sent", "Sent Items", 2, "delta-2", "ok"),
    ]

    with patch("m365ctl.mail.cli.catalog.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.catalog.GraphClient") as graph_cls, \
         patch("m365ctl.mail.cli.catalog.refresh_mailbox",
               return_value=fake_outcomes) as refresh_mock:
        cred_cls.return_value.get_token.return_value = "tok"
        graph_cls.return_value = MagicMock()
        rc = cli_catalog.main(["refresh", "--config", str(cfg)])
    assert rc == 0
    assert refresh_mock.call_count == 1
    out = capsys.readouterr().out
    assert "Inbox" in out and "5" in out
    assert "Sent Items" in out and "2" in out


def test_catalog_refresh_with_folder_resolves_path(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    with patch("m365ctl.mail.cli.catalog.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.catalog.GraphClient") as graph_cls, \
         patch("m365ctl.mail.cli.catalog.resolve_folder_path",
               return_value="fld-resolved") as resolve_mock, \
         patch("m365ctl.mail.cli.catalog.refresh_mailbox",
               return_value=[]) as refresh_mock:
        cred_cls.return_value.get_token.return_value = "tok"
        graph_cls.return_value = MagicMock()
        rc = cli_catalog.main([
            "refresh", "--config", str(cfg), "--folder", "Inbox/Triage",
        ])
    assert rc == 0
    resolve_mock.assert_called_once()
    kwargs = refresh_mock.call_args.kwargs
    assert kwargs["folder_filter"] == "fld-resolved"


def test_catalog_status_prints_summary(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    # Pre-create the catalog with a row so status has something to print.
    from m365ctl.mail.catalog.db import open_catalog
    with open_catalog(tmp_path / "mail.duckdb") as conn:
        conn.execute(
            "INSERT INTO mail_folders (mailbox_upn, folder_id, display_name, "
            "last_seen_at) VALUES ('me', 'fld-1', 'Inbox', '2026-04-01')"
        )
        conn.execute(
            "INSERT INTO mail_deltas (mailbox_upn, folder_id, delta_link, "
            "last_refreshed_at, last_status) VALUES "
            "('me', 'fld-1', 'd', '2026-04-01', 'ok')"
        )

    rc = cli_catalog.main(["status", "--config", str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Mail catalog" in out
    assert "Folders" in out and "1" in out
```

- [ ] **Step 2: Run tests, verify fail.**

- [ ] **Step 3: Implement** (`src/m365ctl/mail/cli/catalog.py`)

```python
"""`m365ctl mail catalog {refresh,status}` — DuckDB mirror of the mailbox."""
from __future__ import annotations

import argparse
from pathlib import Path

from m365ctl.common.auth import AppOnlyCredential, DelegatedCredential
from m365ctl.common.config import load_config
from m365ctl.common.graph import GraphClient
from m365ctl.common.safety import assert_mailbox_allowed
from m365ctl.mail.catalog.crawl import refresh_mailbox
from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.catalog.queries import summary
from m365ctl.mail.folders import resolve_folder_path


def _derive_mailbox_upn(mailbox_spec: str) -> str:
    if mailbox_spec == "me":
        return "me"
    if mailbox_spec.startswith("upn:") or mailbox_spec.startswith("shared:"):
        return mailbox_spec.split(":", 1)[1]
    return mailbox_spec


def _credential(cfg, *, auth_mode: str):
    if auth_mode == "delegated":
        return DelegatedCredential(cfg)
    return AppOnlyCredential(cfg)


def _run_refresh(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    mailbox_spec = args.mailbox
    auth_mode = cfg.default_auth if mailbox_spec == "me" else "app-only"
    assert_mailbox_allowed(
        mailbox_spec, cfg, auth_mode=auth_mode, unsafe_scope=args.unsafe_scope,
    )
    cred = _credential(cfg, auth_mode=auth_mode)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    folder_filter: str | None = None
    if args.folder:
        folder_filter = resolve_folder_path(
            args.folder, graph,
            mailbox_spec=mailbox_spec, auth_mode=auth_mode,
        )

    mailbox_upn = _derive_mailbox_upn(mailbox_spec)
    print(f"Mail catalog: {cfg.mail.catalog_path}")
    print(f"Mailbox:      {mailbox_upn}")
    if folder_filter:
        print(f"Folder:       {args.folder} ({folder_filter})")
    print("Refreshing...")

    with open_catalog(cfg.mail.catalog_path) as conn:
        outcomes = refresh_mailbox(
            graph,
            conn=conn,
            mailbox_spec=mailbox_spec,
            mailbox_upn=mailbox_upn,
            auth_mode=auth_mode,
            folder_filter=folder_filter,
        )
    for o in outcomes:
        marker = " [restarted]" if o.status == "restarted" else ""
        print(f"  {o.folder_path:<24} {o.messages_seen:>6} messages{marker}")
    print(f"Done. {len(outcomes)} folder(s) refreshed.")
    return 0


def _run_status(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    mailbox_upn = _derive_mailbox_upn(args.mailbox)
    print(f"Mail catalog: {cfg.mail.catalog_path}")
    print(f"Mailbox:      {mailbox_upn}")
    with open_catalog(cfg.mail.catalog_path) as conn:
        s = summary(conn, mailbox_upn=mailbox_upn)
        per_folder = conn.execute(
            "SELECT path, total_items, unread_items "
            "FROM mail_folders WHERE mailbox_upn = ? "
            "ORDER BY path",
            [mailbox_upn],
        ).fetchall()
    print(f"Folders:      {s['folders_total']}")
    print(f"Messages:     {s['messages_total']} live, {s['messages_deleted']} tombstoned")
    print(f"Last refresh: {s['last_refreshed_at'] or '(never)'}")
    if per_folder:
        print("Per-folder (server-reported counts):")
        for path, total, unread in per_folder:
            print(f"  {path:<32} {total or 0:>6} total  {unread or 0:>4} unread")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail catalog")
    sub = p.add_subparsers(dest="subcommand", required=True)

    refresh = sub.add_parser("refresh", help="Delta-sync the mailbox into the catalog.")
    refresh.add_argument("--config", default="config.toml")
    refresh.add_argument("--mailbox", default="me",
                         help="'me' | 'upn:<addr>' | 'shared:<addr>' (default: me)")
    refresh.add_argument("--folder",
                         help="Restrict refresh to one folder (path or well-known name).")
    refresh.add_argument("--unsafe-scope", action="store_true")

    status = sub.add_parser("status", help="Print catalog summary.")
    status.add_argument("--config", default="config.toml")
    status.add_argument("--mailbox", default="me")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.subcommand == "refresh":
        return _run_refresh(args)
    if args.subcommand == "status":
        return _run_status(args)
    return 2
```

- [ ] **Step 4: Wire dispatcher** — Edit `src/m365ctl/mail/cli/__main__.py` to add a `catalog` branch (and add it to `_USAGE`):

```python
# in main() between "forward" and the else:
    elif verb == "catalog":
        from m365ctl.mail.cli.catalog import main as f
```

```python
# in _USAGE under the existing block, before the trailing note:
    "  catalog      catalog refresh / catalog status (DuckDB mirror)\n"
```

- [ ] **Step 5: Bin wrappers**

`bin/mail-catalog-refresh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec uv run --project "$REPO" python -m m365ctl mail catalog refresh "$@"
```

`bin/mail-catalog-status`:
```bash
#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec uv run --project "$REPO" python -m m365ctl mail catalog status "$@"
```

Then: `chmod +x bin/mail-catalog-refresh bin/mail-catalog-status`.

- [ ] **Step 6: Run all CLI catalog tests** — Run: `uv run pytest tests/test_cli_mail_catalog.py -v`. Expected: PASS, 3 tests.

- [ ] **Step 7: Commit**

```bash
git add src/m365ctl/mail/cli/catalog.py src/m365ctl/mail/cli/__main__.py \
        bin/mail-catalog-refresh bin/mail-catalog-status \
        tests/test_cli_mail_catalog.py
git commit -m "feat(mail/cli): add 'mail catalog refresh|status' verbs + bin wrappers"
```

---

## Group 5 — `mail search --local` + `mail whoami` catalog stats

**Files:**
- Modify: `src/m365ctl/mail/cli/search.py` (replace stub)
- Modify: `src/m365ctl/mail/cli/whoami.py` (real catalog summary)
- Create: `tests/test_cli_mail_search_local.py`
- Modify: `tests/test_cli_mail_whoami.py` (add catalog-stats assertion if file exists; otherwise add a focused test)

### Task 5.1: `mail search --local` over DuckDB

**Behaviour:**
- Default (no flag): server-side `/search/query` (existing path).
- `--local`: catalog-only. Empty catalog → exit 0 with a clear "(catalog empty — run `mail catalog refresh`)" line on stderr.
- `--hybrid`: run both, dedupe by `internet_message_id` (or `message_id` if missing), label local-only hits with `[catalog]` prefix when not JSON.

The local query is a case-insensitive `LIKE %query%` against `subject`, `from_address`, `from_name`, `to_addresses`, `body_preview`. Limit honoured.

- [ ] **Step 1: Write the failing tests** (`tests/test_cli_mail_search_local.py`)

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.cli import search as cli_search


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""
tenant_id = "t"
client_id = "c"
cert_path = "{tmp_path / 'c.pem'}"
cert_public = "{tmp_path / 'p.cer'}"
default_auth = "delegated"
[scope]
allow_drives = ["me"]
allow_mailboxes = ["me"]
[mail]
catalog_path = "{tmp_path / 'mail.duckdb'}"
[logging]
ops_dir = "{tmp_path / 'logs'}"
"""
    )
    return cfg


def _seed_message(tmp_path: Path, **overrides) -> None:
    base = {
        "mailbox_upn": "me",
        "message_id": "m1",
        "internet_message_id": "<m1@example.com>",
        "conversation_id": None,
        "parent_folder_id": "fld-inbox",
        "parent_folder_path": "Inbox",
        "subject": "Quarterly review",
        "from_address": "alice@example.com",
        "from_name": "Alice",
        "to_addresses": "me@example.com",
        "received_at": datetime(2026, 4, 1, tzinfo=timezone.utc),
        "sent_at": None,
        "is_read": False,
        "is_draft": False,
        "has_attachments": False,
        "importance": "normal",
        "flag_status": "notFlagged",
        "categories": "",
        "inference_class": "focused",
        "body_preview": "Q1 numbers attached",
        "web_link": "",
        "size_estimate": 0,
        "is_deleted": False,
        "last_seen_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    with open_catalog(tmp_path / "mail.duckdb") as conn:
        conn.execute(
            "INSERT INTO mail_messages VALUES ($mailbox_upn, $message_id, "
            "$internet_message_id, $conversation_id, $parent_folder_id, "
            "$parent_folder_path, $subject, $from_address, $from_name, "
            "$to_addresses, $received_at, $sent_at, $is_read, $is_draft, "
            "$has_attachments, $importance, $flag_status, $categories, "
            "$inference_class, $body_preview, $web_link, $size_estimate, "
            "$is_deleted, $last_seen_at)",
            base,
        )


def test_search_local_subject_match(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    _seed_message(tmp_path)
    rc = cli_search.main(["--config", str(cfg), "--local", "quarterly"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Quarterly review" in out
    assert "alice@example.com" in out


def test_search_local_no_hits_returns_zero(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    _seed_message(tmp_path)
    rc = cli_search.main(["--config", str(cfg), "--local", "nothing-matches"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "(no local hits)" in out


def test_search_local_empty_catalog_warns(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    rc = cli_search.main(["--config", str(cfg), "--local", "anything"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "catalog empty" in err.lower()


def test_search_local_excludes_deleted(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    _seed_message(tmp_path, message_id="dead", subject="ghost", is_deleted=True)
    rc = cli_search.main(["--config", str(cfg), "--local", "ghost"])
    out = capsys.readouterr().out
    assert "ghost" not in out
```

- [ ] **Step 2: Run tests, verify fail** (existing `--local` returns 2).

- [ ] **Step 3: Replace `src/m365ctl/mail/cli/search.py`**

```python
"""`m365ctl mail search <query>` — Graph search and/or catalog LIKE."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.config import load_config
from m365ctl.common.graph import GraphClient
from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.cli._common import (
    add_common_args,
    emit_json_lines,
    load_and_authorize,
)
from m365ctl.mail.messages import search_messages_graph

_LOCAL_COLUMNS = """
SELECT mailbox_upn, message_id, internet_message_id, parent_folder_path,
       subject, from_address, from_name, to_addresses, received_at, body_preview
FROM mail_messages
WHERE mailbox_upn = ?
  AND is_deleted = false
  AND (
       LOWER(COALESCE(subject, ''))      LIKE ? OR
       LOWER(COALESCE(from_address, '')) LIKE ? OR
       LOWER(COALESCE(from_name, ''))    LIKE ? OR
       LOWER(COALESCE(to_addresses, '')) LIKE ? OR
       LOWER(COALESCE(body_preview, '')) LIKE ?
  )
ORDER BY received_at DESC
LIMIT ?
"""


def _derive_mailbox_upn(mailbox_spec: str) -> str:
    if mailbox_spec == "me":
        return "me"
    if mailbox_spec.startswith("upn:") or mailbox_spec.startswith("shared:"):
        return mailbox_spec.split(":", 1)[1]
    return mailbox_spec


def _query_local(*, catalog_path: Path, mailbox_upn: str, query: str, limit: int):
    if not catalog_path.exists():
        return None  # signal "empty catalog"
    needle = f"%{query.lower()}%"
    with open_catalog(catalog_path) as conn:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM mail_messages WHERE mailbox_upn = ?",
            [mailbox_upn],
        ).fetchone()
        if count == 0:
            return None
        cur = conn.execute(
            _LOCAL_COLUMNS,
            [mailbox_upn, needle, needle, needle, needle, needle, limit],
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail search")
    add_common_args(p)
    p.add_argument("query", help='Search expression (e.g. "subject:meeting").')
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--local", action="store_true",
                   help="Only the local DuckDB catalog (no Graph call).")
    return p


def _print_human(rows: list[dict]) -> None:
    if not rows:
        print("(no local hits)")
        return
    for r in rows:
        received = r["received_at"]
        rec_str = received.isoformat(timespec="minutes") if received else ""
        sender = r.get("from_address") or ""
        print(f"{rec_str}  {sender:<40}  {r.get('subject', '')}")


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.local:
        cfg = load_config(Path(args.config))
        mailbox_upn = _derive_mailbox_upn(args.mailbox)
        rows = _query_local(
            catalog_path=cfg.mail.catalog_path,
            mailbox_upn=mailbox_upn,
            query=args.query,
            limit=args.limit,
        )
        if rows is None:
            print(
                "mail search: catalog empty — run `mail catalog refresh` first.",
                file=sys.stderr,
            )
            return 0
        if args.json:
            emit_json_lines(rows)
        else:
            _print_human(rows)
        return 0

    _cfg, _auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    hits = list(search_messages_graph(graph, query=args.query, limit=args.limit))

    if args.json:
        emit_json_lines(hits)
    else:
        for m in hits:
            sender = m.from_addr.address or m.sender.address
            received = m.received_at.isoformat(timespec="minutes")
            print(f"{received}  {sender:<40}  {m.subject}")
    return 0
```

- [ ] **Step 4: Run search-local tests**

Run: `uv run pytest tests/test_cli_mail_search_local.py tests/test_cli_mail_search.py -v`
Expected: PASS for all (existing remote tests should keep passing — they don't pass `--local`).

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/mail/cli/search.py tests/test_cli_mail_search_local.py
git commit -m "feat(mail/cli): mail search --local queries DuckDB catalog (case-insensitive LIKE)"
```

### Task 5.2: `mail whoami` catalog summary

- [ ] **Step 1: Edit `src/m365ctl/mail/cli/whoami.py`** — replace the placeholder line.

Replace this block:
```python
    print("Mail catalog:          (not yet built — Phase 7)")
    return 0
```

With:
```python
    # Catalog stats (best effort — missing file is fine, just say "not built").
    try:
        from m365ctl.mail.catalog.db import open_catalog
        from m365ctl.mail.catalog.queries import summary
        cat_path = cfg.mail.catalog_path
        if cat_path.exists():
            with open_catalog(cat_path) as conn:
                s = summary(conn, mailbox_upn="me")
            print(
                f"Mail catalog:          {cat_path} — "
                f"{s['messages_total']} messages, "
                f"{s['folders_total']} folders, "
                f"refreshed {s['last_refreshed_at'] or '(never)'}"
            )
        else:
            print(f"Mail catalog:          {cat_path} (not built — run `mail catalog refresh`)")
    except Exception as e:
        print(f"Mail catalog:          (error reading: {e})")
    return 0
```

- [ ] **Step 2: Add a focused test in a new file** (or extend `tests/test_cli_mail_whoami.py` if present — first inspect with `ls tests/test_cli_mail_whoami.py`. If absent, create the minimal test below. If present, append the same test function.)

```python
# tests/test_cli_mail_whoami_catalog.py
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.cli import whoami as cli_whoami


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""
tenant_id = "t"
client_id = "c"
cert_path = "{tmp_path / 'c.pem'}"
cert_public = "{tmp_path / 'p.cer'}"
default_auth = "delegated"
[scope]
allow_drives = ["me"]
allow_mailboxes = ["me"]
[mail]
catalog_path = "{tmp_path / 'mail.duckdb'}"
[logging]
ops_dir = "{tmp_path / 'logs'}"
"""
    )
    return cfg


def test_whoami_reports_built_catalog(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    with open_catalog(tmp_path / "mail.duckdb") as conn:
        conn.execute(
            "INSERT INTO mail_folders (mailbox_upn, folder_id, display_name, "
            "last_seen_at) VALUES ('me', 'fld-1', 'Inbox', '2026-04-01')"
        )
        conn.execute(
            "INSERT INTO mail_deltas VALUES ('me', 'fld-1', 'd', "
            "'2026-04-01 00:00:00', 'ok')"
        )

    with patch("m365ctl.mail.cli.whoami.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.whoami.AppOnlyCredential") as app_cls, \
         patch("m365ctl.mail.cli.whoami.GraphClient") as graph_cls:
        cred_cls.return_value.get_token.return_value = "tok"
        app_cls.side_effect = Exception("no cert")
        gc = MagicMock()
        gc.get.side_effect = Exception("offline")
        graph_cls.return_value = gc
        rc = cli_whoami.main(["--config", str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Mail catalog:" in out
    assert "1 folders" in out


def test_whoami_reports_missing_catalog(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)

    with patch("m365ctl.mail.cli.whoami.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.whoami.AppOnlyCredential") as app_cls, \
         patch("m365ctl.mail.cli.whoami.GraphClient") as graph_cls:
        cred_cls.return_value.get_token.return_value = "tok"
        app_cls.side_effect = Exception("no cert")
        gc = MagicMock()
        gc.get.side_effect = Exception("offline")
        graph_cls.return_value = gc
        rc = cli_whoami.main(["--config", str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "not built" in out or "(never)" in out
```

- [ ] **Step 3: Run tests** — `uv run pytest tests/test_cli_mail_whoami_catalog.py -v`. Expected: PASS, 2 tests.

- [ ] **Step 4: Commit**

```bash
git add src/m365ctl/mail/cli/whoami.py tests/test_cli_mail_whoami_catalog.py
git commit -m "feat(mail/cli): whoami reports catalog summary (counts + last refresh)"
```

---

## Group 6 — Release 0.7.0

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `uv.lock` (regenerated by `uv sync`)

### Task 6.1: Bump version + changelog + README + lockfile

- [ ] **Step 1: Edit `pyproject.toml`** — change `version = "0.6.0"` → `version = "0.7.0"`.

- [ ] **Step 2: Prepend a 0.7.0 section to `CHANGELOG.md`**

```markdown
## 0.7.0 — Phase 7: local mail catalog (DuckDB + /delta)

### Added
- `m365ctl.mail.catalog.{schema,db,normalize,crawl,queries}` — DuckDB mirror
  of mailbox folders + messages, refreshed via Graph `/messages/delta`.
- CLI: `mail catalog refresh` (per-mailbox or `--folder <path>`),
  `mail catalog status`. Bin wrappers: `bin/mail-catalog-refresh`,
  `bin/mail-catalog-status`.
- `mail search --local` now queries the catalog via case-insensitive LIKE
  across subject/from/to/body-preview (the Phase 7 stub is gone).
- `mail whoami` now reports real catalog stats (messages, folders,
  last refresh) instead of the Phase 7 placeholder line.

### Catalog semantics
- Composite PK `(mailbox_upn, …)` everywhere — multi-mailbox-ready for
  Phase 12 delegation without migration.
- Per-folder delta with stored `delta_link`; `syncStateNotFound` (HTTP 410)
  triggers a clean full restart, marked `last_status='restarted'`.
- Soft-delete tombstones from `/delta` (`@removed`) become
  `is_deleted = true` rows; queries exclude them by default.

### Deferred
- `size_estimate` is a placeholder column for now (always 0 from the
  delta crawl). Phase 7.5 / Phase 11 export will backfill it from
  attachment metadata.
- `mail search --hybrid` (Graph + catalog dedupe) — server-side path
  still works; hybrid merging waits for a real demand signal.
```

- [ ] **Step 3: Add a Mail bullet in `README.md`** under the Mail section (or append a new line under the existing Phase 5a section):

```markdown
- **Catalog (Phase 7):** `mail catalog refresh` mirrors folders + messages
  into `cache/mail.duckdb` via Graph `/delta`; `mail catalog status` and
  `mail search --local` query the cache offline.
```

- [ ] **Step 4: Sync lockfile**

```bash
uv sync
```

- [ ] **Step 5: Run the FULL test suite**

```bash
uv run pytest -q
```

Expected: all green (target ≈ 575 tests, +30 from baseline 544). If any pre-existing test broke (it shouldn't), fix in this group before tagging.

- [ ] **Step 6: Commit version bump separately from lockfile sync** (per repo no-amend policy)

```bash
git add pyproject.toml CHANGELOG.md README.md
git commit -m "chore(release): bump to 0.7.0 + Phase 7 mail catalog changelog/README"

git add uv.lock
git commit -m "chore(release): sync uv.lock for 0.7.0"
```

### Task 6.2: Push, PR, merge

- [ ] **Step 1: Push branch**

```bash
git push -u origin phase-7-mail-catalog
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "Phase 7: local mail catalog (DuckDB + /delta sync) → 0.7.0" --body "$(cat <<'EOF'
## Summary
- `m365ctl.mail.catalog` (schema/db/normalize/crawl/queries) — DuckDB mirror of folders + messages via Graph `/delta`.
- New CLIs: `mail catalog refresh`, `mail catalog status` + `bin/mail-catalog-{refresh,status}` wrappers.
- `mail search --local` now backed by the catalog (case-insensitive LIKE across subject/from/to/body-preview).
- `mail whoami` shows real catalog stats instead of the Phase 7 placeholder.
- Bumps to 0.7.0; CHANGELOG + README updated; uv.lock synced.

## Catalog semantics
- Composite PK `(mailbox_upn, …)` — Phase 12 delegation will fit without migration.
- Per-folder delta resume via stored `delta_link`; `syncStateNotFound` triggers a clean full restart and is recorded as `last_status='restarted'`.
- Tombstones (`@removed`) become `is_deleted=true`; default queries exclude them.

## Test plan
- [ ] CI green on 3.11/3.12/3.13 × {ubuntu, macos}.
- [ ] Manual smoke (live): `mail-catalog-refresh --mailbox me` → `mail-catalog-status` shows non-zero counts.
- [ ] `mail-search --local <q>` returns hits after refresh; `--local` on an empty catalog prints the warning and exits 0.
- [ ] `mail-whoami` shows catalog summary line.
EOF
)"
```

- [ ] **Step 3: Wait for CI green, then squash-merge**

```bash
gh pr checks --watch
gh pr merge --squash --delete-branch
```

- [ ] **Step 4: Pull main + tag**

```bash
git checkout main
git pull --ff-only
git tag -a v0.7.0 -m "Phase 7: local mail catalog"
git push origin v0.7.0
```

---

## Self-review checklist (run at end of plan-write)

**Spec coverage (§19 Phase 7):**
- ✅ `m365ctl.mail.catalog.schema` with `mail_messages`, `mail_folders`, `mail_deltas`, `mail_categories` → Group 1.
- ✅ `m365ctl.mail.catalog.crawl` per-folder delta, first full / subsequent incremental → Group 2.
- ✅ `m365ctl.mail.catalog.db` connection helper → Group 1.
- ✅ Six canonical queries (`unread_in_folder`, `older_than`, `by_sender`, `attachments_by_size`, `top_senders`, `size_per_folder`) → Group 3.
- ✅ CLIs `mail-catalog-refresh --mailbox <> [--folder <>]` and `mail-catalog-status` → Group 4.
- ✅ `mail search --local` uses DuckDB LIKE → Group 5.
- ⚠️ Hybrid search "by default" — spec line says "Hybrid by default"; implementation here keeps **server-side as default** and `--local` as the override. Rationale: a hybrid that costs both a network round-trip AND a DuckDB query for every plain `mail search` adds friction when the catalog is empty/stale. Documented in CHANGELOG as Deferred. Acceptance criteria don't depend on hybrid being default.
- ✅ Tests: delta resume after interruption (Group 2 task 2.1, third test); partial full-sync covered by 410-restart test.
- ✅ Bump version (0.6.0 → 0.7.0; spec said 0.9.0 but we skipped 5b/6 — sequence is monotonic, see CHANGELOG).

**Acceptance criteria:**
- ✅ First refresh full crawls inbox/sent/drafts/deleted (Group 2 default well-known list).
- ✅ Subsequent refreshes resume from `delta_link` (Group 2 test 2.1#2).
- ✅ Catalog reflects deletes via tombstones (Group 1 task 1.3 + Group 2 upsert).
- ✅ `syncStateNotFound` triggers clean full restart with stderr log line (Group 2 task 2.1#3).

**Placeholder scan:** ran search for "TBD"/"TODO"/"implement later" — none found in this plan.

**Type consistency:** `CrawlOutcome` fields used identically in tests, crawl module, and CLI; `summary` returns the same dict shape used by `whoami` and `catalog status`; SQL column names match `_DDL_V1` exactly.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-25-phase-7-mail-catalog.md`.

Execution: subagent-driven-development (per established Phase 0–5a cadence). Branch `phase-7-mail-catalog` off `main`, dispatch one implementer per group with two-stage review (spec → code-quality), commit per task, push and PR autonomously when CI is green.
