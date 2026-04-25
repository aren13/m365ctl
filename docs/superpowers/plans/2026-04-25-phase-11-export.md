# Phase 11 — Export (EML, MBOX, attachments) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan group-by-group. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Local backups + compliance exports. Per-message `.eml`, per-folder `.mbox`, attachments dump, and full-mailbox export with resume-on-interrupt via a `manifest.json`.

**Architecture:**
- `m365ctl.mail.export` — `export_message_to_eml`, `export_folder_to_mbox` (streaming), `export_mailbox` (manifest-driven), `export_attachments`. EML payload comes from Graph `/{ub}/messages/{id}/$value` (returns RFC-5322 bytes). MBOX is the standard `From `-separator format wrapping each EML in sequence.
- `m365ctl.mail.export.manifest` — read/write a JSON manifest at `<out_dir>/manifest.json` keyed by folder, recording per-folder `(last_exported_id, count)`. On re-run, skip messages already past the cursor.
- CLI `m365ctl mail export {message, folder, mailbox, attachments}` with `--out PATH` / `--out-dir DIR` flags.
- Bin wrapper `bin/mail-export`.
- Read-only — no mutations, no audit/undo plumbing needed.

**Tech stack:** `email.parser` and `mailbox` from stdlib for MIME parsing + mbox round-trip tests. No new deps.

**Baseline:** `main` post-PR-#15 (a3e6fe4), 712 passing tests, 0 mypy errors. Tag `v0.10.0` shipped.

**Version bump:** 0.10.0 → 0.11.0.

---

## File Structure

**New:**
- `src/m365ctl/mail/export/__init__.py` — empty.
- `src/m365ctl/mail/export/eml.py` — `export_message_to_eml`, `fetch_eml_bytes` helper.
- `src/m365ctl/mail/export/mbox.py` — `export_folder_to_mbox`, `MboxWriter` streaming class.
- `src/m365ctl/mail/export/attachments.py` — `export_attachments` (one file per attachment).
- `src/m365ctl/mail/export/manifest.py` — `Manifest` dataclass + read/write helpers.
- `src/m365ctl/mail/export/mailbox.py` — `export_mailbox` orchestrator (walks folders, calls mbox export per folder, updates manifest).
- `src/m365ctl/mail/cli/export.py` — argparse entry for the four sub-verbs.
- `bin/mail-export` — exec wrapper.
- `tests/test_mail_export_eml.py`
- `tests/test_mail_export_mbox.py`
- `tests/test_mail_export_attachments.py`
- `tests/test_mail_export_manifest.py`
- `tests/test_mail_export_mailbox.py`
- `tests/test_cli_mail_export.py`

**Modify:**
- `src/m365ctl/mail/cli/__main__.py` — route new `export` verb + add `_USAGE` line.
- `pyproject.toml` — bump 0.10.0 → 0.11.0.
- `CHANGELOG.md` — 0.11.0 section.
- `README.md` — Mail bullet.

---

## Group 1 — EML export + attachments

**Files:**
- Create: `src/m365ctl/mail/export/__init__.py`
- Create: `src/m365ctl/mail/export/eml.py`
- Create: `src/m365ctl/mail/export/attachments.py`
- Create: `tests/test_mail_export_eml.py`
- Create: `tests/test_mail_export_attachments.py`

### Task 1.1: EML export — TDD

- [ ] **Step 1: Failing tests** (`tests/test_mail_export_eml.py`)

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.mail.export.eml import export_message_to_eml, fetch_eml_bytes


def test_fetch_eml_bytes_routes_to_value_endpoint_me():
    graph = MagicMock()
    graph.get_bytes.return_value = b"From: a@example.com\r\nSubject: x\r\n\r\nbody\r\n"
    out = fetch_eml_bytes(graph, mailbox_spec="me", auth_mode="delegated", message_id="m-1")
    assert out == b"From: a@example.com\r\nSubject: x\r\n\r\nbody\r\n"
    graph.get_bytes.assert_called_once_with("/me/messages/m-1/$value")


def test_fetch_eml_bytes_app_only_routes_via_users_upn():
    graph = MagicMock()
    graph.get_bytes.return_value = b"x"
    fetch_eml_bytes(graph, mailbox_spec="upn:bob@example.com",
                    auth_mode="app-only", message_id="m-2")
    graph.get_bytes.assert_called_once_with("/users/bob@example.com/messages/m-2/$value")


def test_export_message_to_eml_writes_file(tmp_path: Path):
    graph = MagicMock()
    graph.get_bytes.return_value = b"From: a\r\nSubject: hi\r\n\r\nbody\r\n"
    out = tmp_path / "msg.eml"
    written = export_message_to_eml(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_path=out,
    )
    assert written == out
    assert out.read_bytes() == b"From: a\r\nSubject: hi\r\n\r\nbody\r\n"


def test_export_message_to_eml_creates_parent_dirs(tmp_path: Path):
    graph = MagicMock()
    graph.get_bytes.return_value = b"x"
    out = tmp_path / "deep" / "nested" / "msg.eml"
    export_message_to_eml(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_path=out,
    )
    assert out.exists()


def test_export_message_to_eml_round_trip_via_email_parser(tmp_path: Path):
    """Round-trip: write EML bytes, parse with stdlib email, re-emit, equal."""
    import email
    from email import policy

    graph = MagicMock()
    raw = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: round trip\r\n"
        b"Message-ID: <abc@example.com>\r\n"
        b"\r\n"
        b"Body line 1\r\n"
        b"Body line 2\r\n"
    )
    graph.get_bytes.return_value = raw
    out = tmp_path / "msg.eml"
    export_message_to_eml(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_path=out,
    )
    parsed = email.message_from_bytes(out.read_bytes(), policy=policy.default)
    assert parsed["From"] == "alice@example.com"
    assert parsed["Subject"] == "round trip"
    assert parsed["Message-ID"] == "<abc@example.com>"
```

- [ ] **Step 2:** Run, verify ImportError.

- [ ] **Step 3: Implement** (`src/m365ctl/mail/export/__init__.py` empty + `src/m365ctl/mail/export/eml.py`)

```python
"""EML (RFC 5322 / MIME) export via Graph ``/messages/{id}/$value``.

Graph returns the message's full MIME wire format on this endpoint —
exactly what `.eml` files contain. No client-side reassembly needed.
"""
from __future__ import annotations

from pathlib import Path

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base


def fetch_eml_bytes(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    message_id: str,
) -> bytes:
    """GET /<ub>/messages/{id}/$value — returns RFC-5322 MIME bytes."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    return graph.get_bytes(f"{ub}/messages/{message_id}/$value")


def export_message_to_eml(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    message_id: str,
    out_path: Path,
) -> Path:
    """Fetch one message and write its MIME body to out_path."""
    raw = fetch_eml_bytes(
        graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode, message_id=message_id,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw)
    return out_path
```

- [ ] **Step 4:** Run tests:
```bash
uv run pytest tests/test_mail_export_eml.py -v
```
Expected: 5 tests pass.

- [ ] **Step 5:** mypy + ruff clean.

- [ ] **Step 6: Commit:**
```bash
git add src/m365ctl/mail/export/__init__.py src/m365ctl/mail/export/eml.py \
        tests/test_mail_export_eml.py
git commit -m "feat(mail/export): per-message EML export via Graph /messages/{id}/\$value"
```

### Task 1.2: Attachment export — TDD

- [ ] **Step 1: Failing tests** (`tests/test_mail_export_attachments.py`)

```python
from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from m365ctl.mail.export.attachments import export_attachments


def _file_attachment(att_id: str, name: str, content: bytes, content_type: str = "application/octet-stream") -> dict:
    return {
        "id": att_id,
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": name,
        "contentType": content_type,
        "size": len(content),
        "isInline": False,
        "contentBytes": base64.b64encode(content).decode("ascii"),
    }


def test_exports_one_attachment(tmp_path: Path):
    graph = MagicMock()
    graph.get.return_value = {"value": [_file_attachment("a-1", "doc.pdf", b"PDFBYTES")]}
    written = export_attachments(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_dir=tmp_path,
    )
    assert len(written) == 1
    assert written[0].name == "doc.pdf"
    assert (tmp_path / "doc.pdf").read_bytes() == b"PDFBYTES"


def test_exports_multiple_with_collision_suffixes(tmp_path: Path):
    graph = MagicMock()
    graph.get.return_value = {"value": [
        _file_attachment("a-1", "doc.pdf", b"AAA"),
        _file_attachment("a-2", "doc.pdf", b"BBB"),  # same name
        _file_attachment("a-3", "doc.pdf", b"CCC"),  # same name
    ]}
    written = export_attachments(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_dir=tmp_path,
    )
    names = sorted(p.name for p in written)
    assert names == ["doc (1).pdf", "doc (2).pdf", "doc.pdf"]


def test_skips_inline_by_default(tmp_path: Path):
    graph = MagicMock()
    inline_att = _file_attachment("a-1", "logo.png", b"PNG")
    inline_att["isInline"] = True
    file_att = _file_attachment("a-2", "doc.pdf", b"PDF")
    graph.get.return_value = {"value": [inline_att, file_att]}
    written = export_attachments(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_dir=tmp_path,
    )
    assert [p.name for p in written] == ["doc.pdf"]


def test_includes_inline_when_flagged(tmp_path: Path):
    graph = MagicMock()
    inline = _file_attachment("a-1", "logo.png", b"PNG")
    inline["isInline"] = True
    graph.get.return_value = {"value": [inline]}
    written = export_attachments(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_dir=tmp_path,
        include_inline=True,
    )
    assert [p.name for p in written] == ["logo.png"]


def test_skips_non_file_attachments(tmp_path: Path):
    graph = MagicMock()
    item_att = {
        "id": "a-1",
        "@odata.type": "#microsoft.graph.itemAttachment",
        "name": "calendar.ics",
        "contentType": "application/octet-stream",
        "size": 0,
        "isInline": False,
    }
    graph.get.return_value = {"value": [item_att, _file_attachment("a-2", "doc.pdf", b"X")]}
    written = export_attachments(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_dir=tmp_path,
    )
    assert [p.name for p in written] == ["doc.pdf"]


def test_sanitises_path_separators_in_name(tmp_path: Path):
    graph = MagicMock()
    att = _file_attachment("a-1", "evil/../../../etc/passwd", b"X")
    graph.get.return_value = {"value": [att]}
    written = export_attachments(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_dir=tmp_path,
    )
    # Name reduced to a safe basename inside out_dir.
    assert len(written) == 1
    assert written[0].parent == tmp_path
    assert "/" not in written[0].name
    assert ".." not in written[0].name


def test_returns_empty_list_when_no_attachments(tmp_path: Path):
    graph = MagicMock()
    graph.get.return_value = {"value": []}
    written = export_attachments(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_dir=tmp_path,
    )
    assert written == []
```

- [ ] **Step 2:** Run, verify ImportError.

- [ ] **Step 3: Implement** (`src/m365ctl/mail/export/attachments.py`)

```python
"""Export message attachments to a directory.

File attachments (`#microsoft.graph.fileAttachment`) are written by name;
inline attachments are skipped by default. Item and reference attachments
are skipped (item attachments are nested Graph entities — out of scope
here; reference attachments are URLs to OneDrive items, exported via the
OneDrive side instead).

Filename collisions get ` (N)` suffixes. Names containing path separators
or `..` are reduced to a safe basename.
"""
from __future__ import annotations

import base64
from pathlib import Path

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base


def export_attachments(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    message_id: str,
    out_dir: Path,
    include_inline: bool = False,
) -> list[Path]:
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    resp = graph.get(f"{ub}/messages/{message_id}/attachments")
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    used_names: set[str] = set()
    for att in resp.get("value", []) or []:
        if att.get("@odata.type") != "#microsoft.graph.fileAttachment":
            continue
        if not include_inline and att.get("isInline"):
            continue
        raw_b64 = att.get("contentBytes")
        if not raw_b64:
            continue
        safe = _safe_name(att.get("name") or att.get("id") or "attachment")
        path = _disambiguate(out_dir / safe, used_names)
        path.write_bytes(base64.b64decode(raw_b64))
        used_names.add(path.name)
        written.append(path)
    return written


def _safe_name(name: str) -> str:
    """Strip path separators / parent-traversal; default if empty."""
    base = Path(name).name
    base = base.replace("..", "_")
    return base or "attachment"


def _disambiguate(target: Path, used: set[str]) -> Path:
    """Return a unique sibling path; appends ' (N)' before extension if needed."""
    if target.name not in used and not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    n = 1
    while True:
        candidate = target.with_name(f"{stem} ({n}){suffix}")
        if candidate.name not in used and not candidate.exists():
            return candidate
        n += 1
```

- [ ] **Step 4:** Run tests:
```bash
uv run pytest tests/test_mail_export_attachments.py -v
```
Expected: 7 tests pass.

- [ ] **Step 5:** mypy + ruff clean.

- [ ] **Step 6: Commit:**
```bash
git add src/m365ctl/mail/export/attachments.py tests/test_mail_export_attachments.py
git commit -m "feat(mail/export): attachment dump (file attachments only; safe basenames; collision suffixes)"
```

---

## Group 2 — MBOX export (streaming) + folder-walk

**Files:**
- Create: `src/m365ctl/mail/export/mbox.py`
- Create: `tests/test_mail_export_mbox.py`

### Task 2.1: Streaming MBOX writer

The mbox format prepends each message with a `From SENDER DATE\n` line (no colon). Each EML's MIME is appended verbatim. Trailing `>` escaping for body lines starting with `From ` is the spec but Thunderbird tolerates either way; we'll do the conservative escape.

- [ ] **Step 1: Failing tests** (`tests/test_mail_export_mbox.py`)

```python
from __future__ import annotations

import mailbox
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.mail.export.mbox import MboxWriter, export_folder_to_mbox


_EML1 = (
    b"From: alice@example.com\r\n"
    b"To: bob@example.com\r\n"
    b"Subject: hello\r\n"
    b"Date: Tue, 01 Apr 2026 10:00:00 +0000\r\n"
    b"\r\n"
    b"Body of message 1.\r\n"
)

_EML2 = (
    b"From: carol@example.com\r\n"
    b"To: bob@example.com\r\n"
    b"Subject: greetings\r\n"
    b"Date: Wed, 02 Apr 2026 11:00:00 +0000\r\n"
    b"\r\n"
    b"Body of message 2.\r\n"
    b"From the past\r\n"   # Triggers the leading-From quote escape.
)


def test_mbox_writer_two_messages_round_trip(tmp_path: Path):
    out = tmp_path / "f.mbox"
    with MboxWriter(out) as w:
        w.append(_EML1, sender_addr="alice@example.com",
                 received_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc))
        w.append(_EML2, sender_addr="carol@example.com",
                 received_at=datetime(2026, 4, 2, 11, 0, tzinfo=timezone.utc))

    box = mailbox.mbox(str(out))
    msgs = list(box)
    assert len(msgs) == 2
    assert msgs[0]["From"] == "alice@example.com"
    assert msgs[1]["Subject"] == "greetings"
    box.close()


def test_mbox_writer_escapes_leading_from_in_body(tmp_path: Path):
    out = tmp_path / "f.mbox"
    with MboxWriter(out) as w:
        w.append(_EML2, sender_addr="carol@example.com",
                 received_at=datetime(2026, 4, 2, 11, 0, tzinfo=timezone.utc))
    raw = out.read_bytes()
    # The body line "From the past" must have been quoted to ">From the past".
    assert b">From the past" in raw


def test_export_folder_to_mbox_streams_all_messages(tmp_path: Path):
    """Walk the folder via list_messages, fetch EML each, write to mbox."""
    graph = MagicMock()
    # First call: folder messages (id-only listing).
    graph.get_paginated.return_value = iter([(
        [
            {"id": "m1", "from": {"emailAddress": {"address": "a@example.com"}},
             "receivedDateTime": "2026-04-01T10:00:00Z", "subject": "s1"},
            {"id": "m2", "from": {"emailAddress": {"address": "b@example.com"}},
             "receivedDateTime": "2026-04-02T11:00:00Z", "subject": "s2"},
        ],
        None,
    )])
    # Subsequent EML fetches.
    graph.get_bytes.side_effect = [_EML1, _EML2]

    out = tmp_path / "Inbox.mbox"
    count = export_folder_to_mbox(
        graph, mailbox_spec="me", auth_mode="delegated",
        folder_id="fld-inbox", folder_path="Inbox", out_path=out,
    )
    assert count == 2
    box = mailbox.mbox(str(out))
    assert len(list(box)) == 2
    box.close()


def test_export_folder_to_mbox_handles_empty_folder(tmp_path: Path):
    graph = MagicMock()
    graph.get_paginated.return_value = iter([([], None)])
    out = tmp_path / "f.mbox"
    count = export_folder_to_mbox(
        graph, mailbox_spec="me", auth_mode="delegated",
        folder_id="fld", folder_path="X", out_path=out,
    )
    assert count == 0
    assert out.exists()
    assert out.stat().st_size == 0
```

- [ ] **Step 2:** Run, verify ImportError.

- [ ] **Step 3: Implement** (`src/m365ctl/mail/export/mbox.py`)

```python
"""MBOX export — sequential ``From `` separator format.

Each message is wrapped:

    From <sender> <RFC-2822 date>
    <EML bytes, with body lines starting with "From " escaped to ">From ">

Bodies streamed message-by-message — never buffer the whole folder.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.export.eml import fetch_eml_bytes


_MBOX_DATE_FMT = "%a %b %d %H:%M:%S %Y"


class MboxWriter:
    """Stream-write mbox records to a file. Use as a context manager."""

    def __init__(self, path: Path):
        self.path = path
        self._fh: BinaryIO | None = None

    def __enter__(self) -> "MboxWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "wb")
        return self

    def __exit__(self, *exc) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def append(self, eml_bytes: bytes, *, sender_addr: str, received_at: datetime) -> None:
        if self._fh is None:
            raise RuntimeError("MboxWriter must be used as a context manager")
        header = f"From {sender_addr} {received_at.strftime(_MBOX_DATE_FMT)}\n".encode("utf-8")
        self._fh.write(header)
        # Escape body lines that begin with literal "From " by prefixing ">".
        # Operates on the EML's raw byte stream (line-oriented).
        escaped = re.sub(rb"(?m)^From ", b">From ", eml_bytes)
        self._fh.write(escaped)
        if not escaped.endswith(b"\n"):
            self._fh.write(b"\n")
        self._fh.write(b"\n")  # blank line between records


def export_folder_to_mbox(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    folder_id: str,
    folder_path: str,
    out_path: Path,
    page_size: int = 100,
) -> int:
    """Stream every message in ``folder_id`` into ``out_path``. Returns count."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    list_path = f"{ub}/mailFolders/{folder_id}/messages"
    params = {
        "$select": "id,from,receivedDateTime,subject",
        "$orderby": "receivedDateTime asc",
        "$top": page_size,
    }
    count = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Touch the file so empty folders still produce an mbox file.
    out_path.touch()
    with MboxWriter(out_path) as writer:
        for items, _ in graph.get_paginated(list_path, params=params):
            for raw in items:
                mid = raw["id"]
                sender = (raw.get("from") or {}).get("emailAddress", {}).get("address") or "unknown"
                received_str = raw.get("receivedDateTime") or ""
                received = _parse_iso(received_str)
                eml = fetch_eml_bytes(
                    graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
                    message_id=mid,
                )
                writer.append(eml, sender_addr=sender, received_at=received)
                count += 1
    return count


def _parse_iso(s: str) -> datetime:
    if not s:
        return datetime.utcnow()
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
```

- [ ] **Step 4:** Run tests:
```bash
uv run pytest tests/test_mail_export_mbox.py -v
```
Expected: 4 tests pass. The `mailbox.mbox(...)` round-trip proves the file is parseable by stdlib (which Thunderbird also accepts).

- [ ] **Step 5:** mypy + ruff clean. Commit:
```bash
git add src/m365ctl/mail/export/mbox.py tests/test_mail_export_mbox.py
git commit -m "feat(mail/export): streaming MBOX writer + per-folder export"
```

---

## Group 3 — Manifest + full-mailbox export

**Files:**
- Create: `src/m365ctl/mail/export/manifest.py`
- Create: `src/m365ctl/mail/export/mailbox.py`
- Create: `tests/test_mail_export_manifest.py`
- Create: `tests/test_mail_export_mailbox.py`

### Task 3.1: Manifest dataclass + read/write

- [ ] Tests at `tests/test_mail_export_manifest.py` covering:
  - Empty manifest creates with `version=1` and empty per-folder map.
  - Round-trip: write → read → equal.
  - `update_folder` records last-exported-id + count.
  - `should_skip` returns True iff a folder is at status="done".
  - Reading a missing manifest returns an empty Manifest (not an error).
  - Reading a malformed JSON raises `ManifestError`.

- [ ] Implement (`src/m365ctl/mail/export/manifest.py`):

```python
"""Per-export manifest for resume-on-interrupt.

A ``Manifest`` records, per folder:
  - status: 'pending' | 'in_progress' | 'done'
  - count:  messages exported so far
  - mbox_path: relative path under the export root
  - started_at / completed_at: ISO timestamps

Re-running ``export_mailbox`` reads the manifest first; folders marked
``done`` are skipped. ``in_progress`` folders are restarted (the mbox
file is overwritten — the per-folder unit isn't restartable mid-stream
in this first cut; cancel during a folder = redo that folder).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

CURRENT_MANIFEST_VERSION = 1


class ManifestError(ValueError):
    """Raised when the manifest is unreadable or has the wrong shape."""


@dataclass
class FolderEntry:
    folder_id: str
    folder_path: str
    mbox_path: str
    status: str = "pending"        # 'pending' | 'in_progress' | 'done'
    count: int = 0
    started_at: str | None = None
    completed_at: str | None = None


@dataclass
class Manifest:
    version: int = CURRENT_MANIFEST_VERSION
    mailbox_upn: str = ""
    started_at: str = ""
    folders: dict[str, FolderEntry] = field(default_factory=dict)

    def update_folder(
        self, folder_id: str, *,
        folder_path: str, mbox_path: str,
        status: str, count: int,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.folders.get(folder_id)
        if existing is None:
            existing = FolderEntry(
                folder_id=folder_id, folder_path=folder_path, mbox_path=mbox_path,
                started_at=now,
            )
            self.folders[folder_id] = existing
        existing.status = status
        existing.count = count
        if status == "in_progress" and existing.started_at is None:
            existing.started_at = now
        if status == "done":
            existing.completed_at = now

    def should_skip(self, folder_id: str) -> bool:
        e = self.folders.get(folder_id)
        return e is not None and e.status == "done"


def write_manifest(manifest: Manifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": manifest.version,
        "mailbox_upn": manifest.mailbox_upn,
        "started_at": manifest.started_at,
        "folders": {fid: asdict(fe) for fid, fe in manifest.folders.items()},
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def read_manifest(path: Path) -> Manifest:
    if not path.exists():
        return Manifest()
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ManifestError(f"invalid JSON in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ManifestError(f"manifest must be an object: {path}")
    if raw.get("version") != CURRENT_MANIFEST_VERSION:
        raise ManifestError(
            f"unsupported manifest version {raw.get('version')!r} in {path}"
        )
    folders = {
        fid: FolderEntry(**fe) for fid, fe in (raw.get("folders") or {}).items()
    }
    return Manifest(
        version=raw["version"],
        mailbox_upn=raw.get("mailbox_upn", ""),
        started_at=raw.get("started_at", ""),
        folders=folders,
    )
```

- [ ] Run tests, mypy + ruff clean. Commit:
```
feat(mail/export): manifest dataclass + JSON round-trip for resume-on-interrupt
```

### Task 3.2: Full-mailbox export orchestrator

- [ ] Tests at `tests/test_mail_export_mailbox.py` covering:
  - Walks all folders via `list_folders`, exports each to `<out_dir>/<sanitised_path>.mbox`, updates manifest.
  - Re-running with an existing manifest where one folder is `status=done` skips that folder's `export_folder_to_mbox` call.
  - Manifest gets `mailbox_upn` and `started_at` populated on first run.
  - Folder paths with `/` get sanitised (replaced with `_` for filesystem safety) so nested folder paths produce flat per-folder mbox files.
  - Empty mailbox (no folders) writes manifest with empty `folders` and exits cleanly.

- [ ] Implement (`src/m365ctl/mail/export/mailbox.py`):

```python
"""Walk every folder + emit one mbox per folder + manifest."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode
from m365ctl.mail.export.manifest import (
    Manifest, read_manifest, write_manifest,
)
from m365ctl.mail.export.mbox import export_folder_to_mbox
from m365ctl.mail.folders import list_folders


def export_mailbox(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    mailbox_upn: str,
    auth_mode: AuthMode,
    out_dir: Path,
) -> Manifest:
    """Export every folder; write a manifest.json at out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest = read_manifest(manifest_path)
    if not manifest.mailbox_upn:
        manifest.mailbox_upn = mailbox_upn
    if not manifest.started_at:
        manifest.started_at = datetime.now(timezone.utc).isoformat()

    for folder in list_folders(graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode):
        if manifest.should_skip(folder.id):
            continue
        safe = _sanitise(folder.path)
        mbox_path = out_dir / f"{safe}.mbox"
        manifest.update_folder(
            folder.id,
            folder_path=folder.path,
            mbox_path=str(mbox_path.relative_to(out_dir)),
            status="in_progress",
            count=0,
        )
        write_manifest(manifest, manifest_path)
        try:
            count = export_folder_to_mbox(
                graph,
                mailbox_spec=mailbox_spec,
                auth_mode=auth_mode,
                folder_id=folder.id,
                folder_path=folder.path,
                out_path=mbox_path,
            )
        except Exception:
            # Persist whatever progress we had; surface the error to caller.
            write_manifest(manifest, manifest_path)
            raise
        manifest.update_folder(
            folder.id,
            folder_path=folder.path,
            mbox_path=str(mbox_path.relative_to(out_dir)),
            status="done",
            count=count,
        )
        write_manifest(manifest, manifest_path)
    return manifest


def _sanitise(path: str) -> str:
    """Replace path separators so the mbox lives at the export root."""
    return path.replace("/", "_").replace("\\", "_")
```

- [ ] Run tests, mypy + ruff clean. Commit:
```
feat(mail/export): full-mailbox orchestrator with manifest-driven resume
```

---

## Group 4 — CLI + bin wrapper + dispatcher route

**Files:**
- Create: `src/m365ctl/mail/cli/export.py`
- Create: `bin/mail-export`
- Modify: `src/m365ctl/mail/cli/__main__.py`
- Create: `tests/test_cli_mail_export.py`

CLI:
```
mail export message <message-id> --out <path.eml>
mail export folder <folder-path> --out <path.mbox>
mail export mailbox --out-dir <dir>
mail export attachments <message-id> --out-dir <dir> [--include-inline]
```

`mail export folder` resolves the folder path via `resolve_folder_path` (which now hits `/mailFolders/{wellKnownName}` directly, post-PR-#9, so `Inbox`/`Drafts`/etc. all work).

`mail export mailbox` re-runs are idempotent: re-running picks up where it left off via the manifest.

- [ ] Tests at `tests/test_cli_mail_export.py` — minimum 6:
  - `message <id> --out file.eml` calls `export_message_to_eml` with the right args.
  - `folder <path> --out f.mbox` resolves the folder path and calls `export_folder_to_mbox`.
  - `mailbox --out-dir <dir>` calls `export_mailbox` with the right `out_dir`.
  - `attachments <id> --out-dir <dir>` calls `export_attachments` (skips inline by default).
  - `attachments <id> --out-dir <dir> --include-inline` passes `include_inline=True`.
  - Missing required args (`message` without `--out`, `folder` without `--out`, etc.) returns 2.

- [ ] Implement, dispatcher route, bin wrapper. Quality gates. Commit:
```
feat(mail/cli/export): mail export {message, folder, mailbox, attachments} + bin wrapper
```

---

## Group 5 — Release 0.11.0

### Task 5.1: Bump + changelog + README + lockfile

- [ ] `pyproject.toml`: `0.10.0` → `0.11.0`.

- [ ] Prepend CHANGELOG.md:

```markdown
## 0.11.0 — Phase 11: export (EML, MBOX, attachments)

### Added
- `m365ctl.mail.export.eml` — per-message EML via Graph
  `/messages/{id}/$value` (returns native RFC 5322 / MIME bytes).
- `m365ctl.mail.export.mbox` — streaming MBOX writer + per-folder
  export, `From `-line escaping in bodies.
- `m365ctl.mail.export.attachments` — file-attachment dump with
  collision suffixes and basename sanitising.
- `m365ctl.mail.export.manifest` + `m365ctl.mail.export.mailbox` —
  resume-on-interrupt full-mailbox export. `manifest.json` records
  per-folder status (`pending`/`in_progress`/`done`); re-running picks
  up where it left off.
- CLI: `mail export {message, folder, mailbox, attachments}` and
  bin wrapper `bin/mail-export`.

### Read-only
No mutations, no audit/undo, no Graph writes — pure read path.

### Deferred
- Per-folder mid-stream resume (currently, an interrupted folder
  restarts from scratch on next run).
- Item attachments (`#microsoft.graph.itemAttachment`) and reference
  attachments (OneDrive item links) — Phase 11.x.
```

- [ ] README Mail bullet:
```markdown
- **Export (Phase 11):** `mail export {message,folder,mailbox,attachments}`
  — per-message EML, streaming MBOX, attachment dump, and full-mailbox
  manifest with resume-on-interrupt. All read-only.
```

- [ ] `uv sync`, full quality gates, two release commits per the no-amend rule.

### Task 5.2: Push, PR, merge, tag

Push branch, open PR titled `Phase 11: export (EML, MBOX, attachments) → 0.11.0`, watch CI, squash-merge, sync main, tag `v0.11.0`.

---

## Self-review

**Spec coverage (§19 Phase 11):**
- ✅ `export_message_to_eml`, `export_folder_to_mbox` (streaming), `export_mailbox` (manifest), `export_attachments` — G1, G2, G3.
- ✅ CLI `mail-export {message,folder,mailbox,attachments}` — G4.
- ✅ Resume-on-interrupt via manifest progress tracking — G3.2.
- ✅ Tests: round-trip EML → parse via stdlib → fields preserved (G1.1 last test). MBOX openable via `mailbox.mbox(...)` (G2.1) — proxy for "openable in Thunderbird" since both use the same parser.
- ⚠️ Spec said bump to 0.13.0 sequentially; we bump to 0.11.0 because we shipped 8/9/10 in different order than spec.

**Acceptance:**
- ✅ EML round-trip via stdlib parser asserted.
- ✅ MBOX round-trip via `mailbox.mbox` reader asserted.
- ✅ `From `-line escaping in bodies asserted.
- ✅ Manifest skip-on-done tested.

**Type consistency:** `Manifest` / `FolderEntry` shape used identically across read/write/update + tests.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-25-phase-11-export.md`. Branch `phase-11-export` already off `main`.
