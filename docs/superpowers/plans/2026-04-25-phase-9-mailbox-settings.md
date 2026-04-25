# Phase 9 — Mailbox Settings (OOO, signature, timezone, working hours) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan group-by-group. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Configure the mailbox via Graph `/mailboxSettings` PATCHes (timezone, working hours, automatic-replies/OOO) plus a local-file signature fallback. All mutations audit-logged + undoable. OOO durations longer than 60 days require TTY confirmation.

**Architecture:**
- `m365ctl.mail.settings` extended with `update_settings(...)`, `set_auto_reply(...)`, `set_timezone(...)`, `set_working_hours(...)` — thin wrappers around `/mailboxSettings` PATCH.
- `m365ctl.mail.signature` — new module. Reads/writes `[mail].signature_path` (text or HTML). Documented Graph-side caveat: roaming signatures are beta + flaky, so this is local-only by default.
- `m365ctl.mail.mutate.settings` — new module. Each setter wraps the underlying API call with audit logger + the existing `dataclasses.replace`-style "before" capture. Inverses registered in `mail.mutate.undo` so `m365ctl undo <op-id>` rolls back any of these.
- CLI extensions to existing `m365ctl mail settings` (already has `show` + `ooo`-as-printer):
  - Replace `ooo` printer with `ooo on|off [--message TEXT] [--audience none|contactsOnly|all] [--start ISO] [--end ISO]`.
  - Add `timezone <tz>` setter, `working-hours <body>` setter (YAML body for clarity).
  - Add `signature show` / `signature set --from-file <path>` subcommand to top-level mail dispatcher (`m365ctl mail signature ...`).
- New bin wrappers: `bin/mail-ooo`, `bin/mail-signature`.

**Tech stack:** Existing GraphClient.patch, existing audit/undo plumbing, PyYAML (already a dep). No new deps.

**Baseline:** `main` post-PR-#14 (cc8f1b9), 674 passing tests, 0 mypy errors. Tag `v0.9.0` shipped.

**Version bump:** 0.9.0 → 0.10.0.

---

## File Structure

**New:**
- `src/m365ctl/mail/signature.py` — local signature read/write + content-type detection.
- `src/m365ctl/mail/mutate/settings.py` — `execute_set_timezone`, `execute_set_working_hours`, `execute_set_auto_reply`, `execute_set_signature`. Each returns `SettingsResult` (status/error/after) and logs audit start/end.
- `src/m365ctl/mail/cli/ooo.py` — argparse for `mail ooo {on,off,show}`.
- `src/m365ctl/mail/cli/signature.py` — argparse for `mail signature {show,set}`.
- `bin/mail-ooo` — exec wrapper.
- `bin/mail-signature` — exec wrapper.
- `tests/test_mail_settings_mutate.py`
- `tests/test_mail_signature.py`
- `tests/test_cli_mail_ooo.py`
- `tests/test_cli_mail_signature.py`
- `tests/test_cli_mail_settings_setters.py`

**Modify:**
- `src/m365ctl/mail/settings.py` — add `update_settings(graph, *, mailbox_spec, auth_mode, body) -> MailboxSettings` and `set_auto_reply(...)`-style wrappers (thin layer over PATCH).
- `src/m365ctl/mail/cli/settings.py` — add `timezone` + `working-hours` subcommands; remove `ooo` printer (it moves to its own dispatcher entry as both reader and setter).
- `src/m365ctl/mail/cli/__main__.py` — route `ooo` and `signature` verbs.
- `src/m365ctl/mail/mutate/undo.py` — register inverses for `mail.settings.*`.
- `pyproject.toml` — bump 0.9.0 → 0.10.0.
- `CHANGELOG.md` — 0.10.0 section.
- `README.md` — Mail bullet.

---

## Group 1 — Mutate executors (timezone, working-hours, auto-reply)

**Files:**
- Modify: `src/m365ctl/mail/settings.py`
- Create: `src/m365ctl/mail/mutate/settings.py`
- Create: `tests/test_mail_settings_mutate.py`

### Task 1.1: Settings PATCH wrappers + executor TDD

- [ ] **Step 1: Failing tests** (`tests/test_mail_settings_mutate.py`)

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from m365ctl.common.audit import AuditLogger
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.mutate.settings import (
    execute_set_auto_reply,
    execute_set_timezone,
    execute_set_working_hours,
    OOOTooLong,
)


def _op(action: str, args: dict) -> Operation:
    return Operation(
        op_id=new_op_id(),
        action=action,
        drive_id="me",
        item_id="",
        args=args,
        dry_run_result="",
    )


def test_set_timezone_patches_graph(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"timeZone": "Europe/Istanbul"}
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.settings.timezone", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "timezone": "Europe/Istanbul",
    })
    r = execute_set_timezone(op, graph, logger, before={"timeZone": "Turkey Standard Time"})
    assert r.status == "ok"
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"timeZone": "Europe/Istanbul"}


def test_set_working_hours_patches(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {}
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    body = {
        "daysOfWeek": ["monday", "tuesday", "wednesday", "thursday", "friday"],
        "startTime": "09:00:00",
        "endTime": "17:00:00",
        "timeZone": {"name": "Europe/Istanbul"},
    }
    op = _op("mail.settings.working-hours", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "working_hours": body,
    })
    r = execute_set_working_hours(op, graph, logger, before={})
    assert r.status == "ok"
    sent = graph.patch.call_args.kwargs["json_body"]
    assert sent == {"workingHours": body}


def test_set_auto_reply_disabled(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {}
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.settings.auto-reply", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "auto_reply": {"status": "disabled"},
    })
    r = execute_set_auto_reply(op, graph, logger, before={})
    assert r.status == "ok"
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"automaticRepliesSetting": {"status": "disabled"}}


def test_set_auto_reply_scheduled_short_ok(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {}
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=10)
    op = _op("mail.settings.auto-reply", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "auto_reply": {
            "status": "scheduled",
            "scheduledStartDateTime": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "scheduledEndDateTime": {"dateTime": end.isoformat(), "timeZone": "UTC"},
            "internalReplyMessage": "OOO short",
            "externalReplyMessage": "OOO short",
            "externalAudience": "all",
        },
    })
    r = execute_set_auto_reply(op, graph, logger, before={})
    assert r.status == "ok"


def test_set_auto_reply_scheduled_too_long_raises(tmp_path):
    graph = MagicMock()
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=61)   # > 60
    op = _op("mail.settings.auto-reply", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "auto_reply": {
            "status": "scheduled",
            "scheduledStartDateTime": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "scheduledEndDateTime": {"dateTime": end.isoformat(), "timeZone": "UTC"},
            "internalReplyMessage": "x",
            "externalReplyMessage": "x",
            "externalAudience": "all",
        },
    })
    with pytest.raises(OOOTooLong, match="61"):
        execute_set_auto_reply(op, graph, logger, before={})


def test_executor_propagates_graph_error_as_status(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.patch.side_effect = GraphError("InvalidRequest: bad timezone")
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.settings.timezone", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "timezone": "Not-A-Zone",
    })
    r = execute_set_timezone(op, graph, logger, before={})
    assert r.status == "error"
    assert "InvalidRequest" in (r.error or "")
```

- [ ] **Step 2:** Run, verify ImportError.

- [ ] **Step 3: Extend `src/m365ctl/mail/settings.py`** — add update wrapper:

```python
def update_mailbox_settings(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    body: dict,
) -> MailboxSettings:
    """PATCH /mailboxSettings; returns refreshed settings.

    Caller passes a Graph-shaped body (camelCase keys: ``timeZone``,
    ``workingHours``, ``automaticRepliesSetting``, etc.). No translation
    happens here — that's the executor's job.
    """
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    raw = graph.patch(f"{ub}/mailboxSettings", json_body=body)
    return MailboxSettings.from_graph_json(raw)
```

- [ ] **Step 4: Implement** (`src/m365ctl/mail/mutate/settings.py`)

```python
"""Mailbox settings mutators with audit + undo support.

Wraps the existing ``mail.settings.update_mailbox_settings`` PATCH path
in three executors:
  - ``execute_set_timezone``       (mailboxSettings.timeZone)
  - ``execute_set_working_hours``  (mailboxSettings.workingHours)
  - ``execute_set_auto_reply``     (mailboxSettings.automaticRepliesSetting)

Each writes one ``begin``/``end`` audit pair and returns
``SettingsResult(status, error, after)``.

OOO durations longer than 60 days raise ``OOOTooLong`` so the CLI can
intercept and require an explicit TTY confirm before re-dispatching with
the bypass flag.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as _tz
from typing import Any

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.endpoints import user_base


_MAX_OOO_DAYS = 60


class OOOTooLong(RuntimeError):
    """Raised when scheduled OOO duration exceeds the safety threshold."""


@dataclass
class SettingsResult:
    op_id: str
    status: str  # "ok" | "error"
    error: str | None = None
    after: dict[str, Any] = field(default_factory=dict)


def _settings_path(mailbox_spec: str, auth_mode: str) -> str:
    ub = user_base(mailbox_spec, auth_mode=auth_mode)  # type: ignore[arg-type]
    return f"{ub}/mailboxSettings"


def _patch(graph: GraphClient, path: str, *, body: dict) -> dict:
    return graph.patch(path, json_body=body)


def _do(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    cmd: str,
    body: dict,
    before: dict,
) -> SettingsResult:
    path = _settings_path(op.args["mailbox_spec"], op.args["auth_mode"])
    log_mutation_start(logger, cmd=cmd, op=op, before=before)
    try:
        after = _patch(graph, path, body=body)
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, status="error", error=str(e))
        return SettingsResult(op_id=op.op_id, status="error", error=str(e))
    log_mutation_end(logger, op_id=op.op_id, status="ok", after=after)
    return SettingsResult(op_id=op.op_id, status="ok", after=after)


def execute_set_timezone(
    op: Operation, graph: GraphClient, logger: AuditLogger, *, before: dict,
) -> SettingsResult:
    body = {"timeZone": op.args["timezone"]}
    return _do(op, graph, logger, cmd="mail-settings-timezone", body=body, before=before)


def execute_set_working_hours(
    op: Operation, graph: GraphClient, logger: AuditLogger, *, before: dict,
) -> SettingsResult:
    body = {"workingHours": op.args["working_hours"]}
    return _do(op, graph, logger, cmd="mail-settings-working-hours", body=body, before=before)


def execute_set_auto_reply(
    op: Operation, graph: GraphClient, logger: AuditLogger, *, before: dict,
) -> SettingsResult:
    ar = op.args["auto_reply"]
    if ar.get("status") == "scheduled" and not op.args.get("force"):
        days = _ooo_duration_days(ar)
        if days is not None and days > _MAX_OOO_DAYS:
            raise OOOTooLong(
                f"OOO duration is {days} days (>{_MAX_OOO_DAYS}); "
                f"set args['force'] = True to bypass"
            )
    body = {"automaticRepliesSetting": ar}
    return _do(op, graph, logger, cmd="mail-settings-auto-reply", body=body, before=before)


def _ooo_duration_days(ar: dict) -> int | None:
    """Compute scheduled-end minus scheduled-start in days (rounded up)."""
    s = (ar.get("scheduledStartDateTime") or {}).get("dateTime")
    e = (ar.get("scheduledEndDateTime") or {}).get("dateTime")
    if not s or not e:
        return None
    s_dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    e_dt = datetime.fromisoformat(e.replace("Z", "+00:00"))
    if s_dt.tzinfo is None:
        s_dt = s_dt.replace(tzinfo=_tz.utc)
    if e_dt.tzinfo is None:
        e_dt = e_dt.replace(tzinfo=_tz.utc)
    seconds = (e_dt - s_dt).total_seconds()
    if seconds <= 0:
        return 0
    # Ceiling division so 60.5 days -> 61 (triggers safety gate).
    days = int((seconds + 86399) // 86400)
    return days
```

- [ ] **Step 4 (continued):** Verify the audit-logger API. The other mutators use `log_mutation_start(logger, cmd=..., op=..., before=...)` and `log_mutation_end(logger, op_id=..., status=..., after=...|error=...)`. Match that exactly. If the actual API differs (look at `src/m365ctl/common/audit.py` and any existing mutator like `mail/mutate/move.py`), conform.

- [ ] **Step 5:** Run tests:
```bash
uv run pytest tests/test_mail_settings_mutate.py -v
```
Expected: 6 tests pass.

- [ ] **Step 6:** mypy + ruff clean.

- [ ] **Step 7: Commit:**
```bash
git add src/m365ctl/mail/settings.py src/m365ctl/mail/mutate/settings.py tests/test_mail_settings_mutate.py
git commit -m "feat(mail/mutate): timezone/working-hours/auto-reply executors with 60d OOO safety gate"
```

### Task 1.2: Inverse registration in undo

- [ ] **Step 1:** Inspect `src/m365ctl/mail/mutate/undo.py` `build_reverse_mail_operation`. Add three branches:
  - `mail.settings.timezone` → reverse is `mail.settings.timezone` with `timezone=before["timeZone"]`.
  - `mail.settings.working-hours` → reverse is `mail.settings.working-hours` with `working_hours=before` (the prior workingHours dict — record it whole at execute time).
  - `mail.settings.auto-reply` → reverse is `mail.settings.auto-reply` with `auto_reply=before["automaticRepliesSetting"]`. **Set `force: True`** in the reverse op so a long restored OOO doesn't trip the safety gate (it was already approved when originally set).

- [ ] **Step 2:** Tests in `tests/test_mail_mutate_undo_settings.py` — one per branch, asserting the reverse op's `action` and `args` shape.

- [ ] **Step 3:** Run `uv run pytest tests/test_mail_mutate_undo_settings.py -v` → expect 3 pass.

- [ ] **Step 4: Commit:**
```bash
git add src/m365ctl/mail/mutate/undo.py tests/test_mail_mutate_undo_settings.py
git commit -m "feat(mail/mutate/undo): inverses for mail.settings.{timezone,working-hours,auto-reply}"
```

---

## Group 2 — Local signature module + executor

**Files:**
- Create: `src/m365ctl/mail/signature.py`
- Modify: `src/m365ctl/mail/mutate/settings.py` (add `execute_set_signature`)
- Modify: `src/m365ctl/mail/mutate/undo.py` (register inverse)
- Create: `tests/test_mail_signature.py`

### Task 2.1: Signature read/write + executor

**Design:**
- Signature lives at `cfg.mail.signature_path` (already in config). If unset → return empty / refuse to write.
- File extension drives content type: `.html` → HTML, anything else → text.
- The Graph "roaming signatures" endpoint is unstable beta; we don't ship Graph integration this phase. The signature is local-only with a documented caveat in CHANGELOG.
- The executor records the prior signature contents into `before` so `mail undo` restores the old text.

- [ ] **Step 1: Failing tests** (`tests/test_mail_signature.py`)

```python
from __future__ import annotations

from pathlib import Path

import pytest

from m365ctl.common.audit import AuditLogger
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.mutate.settings import execute_set_signature
from m365ctl.mail.signature import (
    SignatureNotConfigured, SignatureReadError, get_signature, set_signature,
)


def test_get_signature_reads_text_file(tmp_path):
    p = tmp_path / "sig.txt"
    p.write_text("Best,\nA")
    sig = get_signature(p)
    assert sig.content_type == "text"
    assert sig.content == "Best,\nA"


def test_get_signature_reads_html_file(tmp_path):
    p = tmp_path / "sig.html"
    p.write_text("<p>Best,</p>")
    sig = get_signature(p)
    assert sig.content_type == "html"
    assert sig.content == "<p>Best,</p>"


def test_get_signature_missing_returns_empty(tmp_path):
    sig = get_signature(tmp_path / "absent.txt")
    assert sig.content == ""
    assert sig.content_type == "text"


def test_get_signature_none_path_raises():
    with pytest.raises(SignatureNotConfigured):
        get_signature(None)


def test_set_signature_writes_file(tmp_path):
    p = tmp_path / "sig.html"
    set_signature(p, content="<p>X</p>")
    assert p.read_text() == "<p>X</p>"


def test_set_signature_creates_parent_dirs(tmp_path):
    p = tmp_path / "deep" / "nested" / "sig.txt"
    set_signature(p, content="X")
    assert p.read_text() == "X"


def test_set_signature_none_path_raises():
    with pytest.raises(SignatureNotConfigured):
        set_signature(None, content="x")


def test_executor_writes_signature(tmp_path):
    p = tmp_path / "sig.txt"
    p.write_text("old")
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = Operation(
        op_id=new_op_id(),
        action="mail.settings.signature",
        drive_id="me",
        item_id="",
        args={"signature_path": str(p), "content": "new"},
        dry_run_result="",
    )
    r = execute_set_signature(op, logger=logger, before={"content": "old"})
    assert r.status == "ok"
    assert p.read_text() == "new"


def test_executor_records_old_content_in_before(tmp_path):
    """Caller responsibility: pass before with prior content; executor uses it for audit."""
    p = tmp_path / "sig.txt"
    p.write_text("v1")
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = Operation(
        op_id=new_op_id(), action="mail.settings.signature",
        drive_id="me", item_id="", args={"signature_path": str(p), "content": "v2"},
        dry_run_result="",
    )
    execute_set_signature(op, logger=logger, before={"content": "v1"})
    # Audit log file contains a 'before' record with content="v1".
    log_files = list((tmp_path / "ops").glob("*.jsonl"))
    assert log_files, "audit log should be written"
    assert "v1" in log_files[0].read_text()
```

- [ ] **Step 2:** Run, verify ImportError.

- [ ] **Step 3: Implement** (`src/m365ctl/mail/signature.py`)

```python
"""Local-file signature read/write.

Phase 9 ships local-only signature management: the signature lives at
``[mail].signature_path`` in config.toml. File extension determines the
content type — ``.html`` is HTML, anything else (`.txt`, no extension)
is plain text.

Sync-to-Outlook (Graph beta endpoint ``/me/userConfiguration`` for
roaming signatures) is documented but not implemented — the API is
flagged unstable. Manual sync from this file remains the user's
responsibility for now.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class SignatureNotConfigured(ValueError):
    """Raised when [mail].signature_path is unset."""


class SignatureReadError(IOError):
    """Raised when the signature file exists but can't be read."""


@dataclass(frozen=True)
class Signature:
    content_type: str   # "text" | "html"
    content: str


def _content_type_for(path: Path) -> str:
    return "html" if path.suffix.lower() in {".html", ".htm"} else "text"


def get_signature(path: Path | None) -> Signature:
    if path is None:
        raise SignatureNotConfigured(
            "[mail].signature_path is not set in config.toml"
        )
    if not path.exists():
        return Signature(content_type=_content_type_for(path), content="")
    try:
        return Signature(
            content_type=_content_type_for(path),
            content=path.read_text(encoding="utf-8"),
        )
    except OSError as e:
        raise SignatureReadError(f"cannot read {path}: {e}") from e


def set_signature(path: Path | None, *, content: str) -> None:
    if path is None:
        raise SignatureNotConfigured(
            "[mail].signature_path is not set in config.toml"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
```

- [ ] **Step 4: Append to `src/m365ctl/mail/mutate/settings.py`:**

```python
def execute_set_signature(
    op: Operation, *, logger: AuditLogger, before: dict,
) -> SettingsResult:
    """Write a local signature file; pure-local (no Graph call)."""
    from m365ctl.mail.signature import set_signature

    path = Path(op.args["signature_path"])
    content = op.args["content"]
    log_mutation_start(logger, cmd="mail-signature-set", op=op, before=before)
    try:
        set_signature(path, content=content)
    except Exception as e:
        log_mutation_end(logger, op_id=op.op_id, status="error", error=str(e))
        return SettingsResult(op_id=op.op_id, status="error", error=str(e))
    log_mutation_end(logger, op_id=op.op_id, status="ok",
                     after={"signature_path": str(path), "bytes": len(content)})
    return SettingsResult(op_id=op.op_id, status="ok",
                          after={"signature_path": str(path)})
```

Imports to add at the top of mutate/settings.py:
```python
from pathlib import Path
```

- [ ] **Step 5: Add inverse** in `mail/mutate/undo.py`:
  - `mail.settings.signature` → reverse is `mail.settings.signature` with `content=before["content"]`.

- [ ] **Step 6:** Run tests:
```bash
uv run pytest tests/test_mail_signature.py -v
```
Expected: 9 tests pass.

- [ ] **Step 7:** mypy + ruff clean. Commit:
```bash
git add src/m365ctl/mail/signature.py src/m365ctl/mail/mutate/settings.py \
        src/m365ctl/mail/mutate/undo.py tests/test_mail_signature.py
git commit -m "feat(mail/signature): local-file signature module + executor + undo registration"
```

---

## Group 3 — CLI subcommands

**Files:**
- Modify: `src/m365ctl/mail/cli/settings.py` (add `timezone`, `working-hours`)
- Create: `src/m365ctl/mail/cli/ooo.py`
- Create: `src/m365ctl/mail/cli/signature.py`
- Modify: `src/m365ctl/mail/cli/__main__.py` (route `ooo` + `signature`)
- Create: `bin/mail-ooo`, `bin/mail-signature`
- Create: `tests/test_cli_mail_ooo.py`, `tests/test_cli_mail_signature.py`, `tests/test_cli_mail_settings_setters.py`

### Task 3.1: `mail settings timezone <tz>` and `mail settings working-hours --from-file <yaml>`

These extend the existing `mail settings` CLI which currently has `show` + `ooo` (printer). Remove the existing `ooo` printer here (it moves to its own dispatcher entry).

CLI:
```
mail settings show
mail settings timezone <tz> --confirm
mail settings working-hours --from-file <yaml> --confirm
```

The working-hours YAML is structured as:
```yaml
days_of_week: [monday, tuesday, wednesday, thursday, friday]
start_time: "09:00:00"
end_time: "17:00:00"
time_zone: "Europe/Istanbul"
```

CLI translates snake_case YAML → camelCase Graph body before calling `execute_set_working_hours`.

- [ ] Tests at `tests/test_cli_mail_settings_setters.py` — minimum 4: timezone success path, working-hours success path with YAML translation, missing `--confirm` returns 2, malformed working-hours YAML rejected with clear error.

- [ ] Implement, mypy + ruff clean, commit:
```
feat(mail/cli/settings): timezone + working-hours setters with --confirm gating
```

### Task 3.2: `mail ooo {on, off, show}`

CLI:
```
mail ooo show                                # prints current auto-reply
mail ooo off --confirm                       # status=disabled
mail ooo on --message TEXT [--audience all|contactsOnly|none]
                          [--start ISO] [--end ISO]
                          [--external-message TEXT]
                          [--force]                # bypass 60d gate
                          --confirm
```

Behaviour:
- `mail ooo show` → fetch + print `AutomaticRepliesSetting`.
- `mail ooo off` → PATCH with `{status: "disabled"}`.
- `mail ooo on` without `--start`/`--end` → `{status: "alwaysEnabled", externalAudience: …, internalReplyMessage: …, externalReplyMessage: …}`.
- `mail ooo on` with `--start`/`--end` → `{status: "scheduled", scheduledStartDateTime: …, scheduledEndDateTime: …, ...}`.
- If `OOOTooLong` raised: print stderr `"OOO duration N days exceeds 60-day safety gate. Re-run with --force to confirm."` and return 1. (TTY-confirm could be added later; the `--force` flag is the simpler, scriptable contract.)

- [ ] Tests at `tests/test_cli_mail_ooo.py` — minimum 5:
  - `show` prints fields.
  - `off --confirm` calls `execute_set_auto_reply` with `disabled` body.
  - `on --message ... --audience all --confirm` builds the alwaysEnabled body.
  - `on --start ... --end ... --message ... --confirm` builds the scheduled body, passes to executor.
  - `on --start X --end (X+61d) --message ... --confirm` returns 1 with stderr mentioning `--force` (because the CLI passes `force: False` and the executor raises `OOOTooLong`; CLI catches and reports).
  - `on ... --force --confirm` succeeds even when duration > 60 days.

- [ ] Implement, dispatcher route + bin wrapper (`bin/mail-ooo`). Commit:
```
feat(mail/cli/ooo): mail ooo {on,off,show} with 60d safety gate and --force bypass
```

### Task 3.3: `mail signature {show, set --from-file PATH}`

CLI:
```
mail signature show
mail signature set --from-file <path>          # text or html depending on extension
                   [--content "inline"]         # mutually exclusive with --from-file
                   --confirm
```

`mail signature show` reads from `cfg.mail.signature_path` and prints content + content_type.

`mail signature set --from-file path.html` reads the file and writes via `execute_set_signature` (which writes to `cfg.mail.signature_path`, NOT the source file). This way users can `mail signature set --from-file ~/draft.html` and it gets committed to the configured path.

If `cfg.mail.signature_path` is unset, both `show` and `set` return 2 with a clear stderr message: `"signature_path not configured in config.toml under [mail]"`.

- [ ] Tests at `tests/test_cli_mail_signature.py` — minimum 4:
  - `show` reads the configured path and prints content.
  - `show` with `signature_path` unset returns 2.
  - `set --from-file` reads source, writes to configured path.
  - `set --content "x" --confirm` inline path.

- [ ] Implement, dispatcher route + bin wrapper (`bin/mail-signature`). Commit:
```
feat(mail/cli/signature): mail signature {show,set --from-file|--content}
```

### Task 3.4: Quality gates after Group 3

After all three CLI commits:
- pytest: ~692 passing, 0 mypy, ruff clean.
- All bin wrappers `+x`.

---

## Group 4 — Release 0.10.0

### Task 4.1: Bump + changelog + README

- [ ] `pyproject.toml`: 0.9.0 → 0.10.0.

- [ ] Prepend CHANGELOG.md:

```markdown
## 0.10.0 — Phase 9: mailbox settings (OOO, signature, timezone, working hours)

### Added
- `m365ctl.mail.settings.update_mailbox_settings` — generic /mailboxSettings PATCH wrapper.
- `m365ctl.mail.mutate.settings` — executors for timezone, workingHours, automaticRepliesSetting (OOO), and local signature. All audit-logged + undoable via `m365ctl undo <op-id>`.
- `m365ctl.mail.signature` — local-file signature module. Content type derived from extension (`.html`/`.htm` → HTML, else text).
- CLI verbs:
  - `mail settings timezone <tz> --confirm`
  - `mail settings working-hours --from-file <yaml> --confirm`
  - `mail ooo {show, on, off}` — full automatic-replies management with `--start`/`--end` scheduled-OOO support.
  - `mail signature {show, set}` — read/write the configured signature file.
- Bin wrappers `bin/mail-ooo`, `bin/mail-signature`.

### Safety
- Scheduled-OOO durations longer than 60 days raise `OOOTooLong`; CLI exits 1 with a clear instruction to re-run with `--force`. Manual mass-OOO accidents (e.g. `--end` typo'd as `2030`) caught before they hit the wire.

### Deferred
- Graph roaming-signatures (`/me/userConfiguration` beta) sync — endpoint is unstable; current implementation is local-only with a documented caveat.
- TTY-confirm flow for OOO long-duration override (we ship `--force` instead; cleaner for scripted use).
```

- [ ] README Mail bullet:
```markdown
- **Mailbox settings (Phase 9):** `mail settings {timezone, working-hours}`,
  `mail ooo {show, on, off}` with scheduled-OOO + 60-day safety gate, and
  `mail signature {show, set}` over a local-file fallback. All mutations
  audit-logged and undoable.
```

- [ ] `uv sync`, full quality gates, two release commits per the no-amend rule.

### Task 4.2: Push, PR, merge, tag

Push branch, open PR titled `Phase 9: mailbox settings (OOO, signature, timezone, working hours) → 0.10.0`, watch CI, squash-merge, sync main, tag `v0.10.0`.

---

## Self-review checklist

**Spec coverage (§19 Phase 9):**
- ✅ `m365ctl.mail.settings`: get/update, auto-reply get/set, signature get/set with caveat — G1, G2.
- ✅ Fallback: signature stored locally at `[mail].signature_path`; sync-to-Outlook documented as best-effort — G2 + CHANGELOG.
- ✅ CLI: `mail-settings show`, `mail-ooo {on,off}`, `mail-signature {show,set}`, `mail-settings timezone`, `mail-settings working-hours` — G3.
- ⚠️ Spec says OOO duration > 60 days → TTY confirm. We ship `--force` flag instead; documented in CHANGELOG. The behavioural contract (block by default, require explicit override) is preserved.
- ✅ Tests: round-trip OOO via `m365ctl undo` — G1.2 inverse + integration via the existing undo dispatcher.
- ⚠️ Spec said bump to 0.11.0 sequentially; we bump to 0.10.0 because we skipped 5b/6 and shipped 8 before 10.

**Acceptance:**
- ✅ Round-trip OOO: undo restores prior `automaticRepliesSetting` (the executor records `before` and the inverse PATCHes it back; `force: True` set on the reverse so the safety gate doesn't block restore).
- ✅ All four PATCH targets covered: timezone, workingHours, automaticRepliesSetting, signature.

**Type consistency:** `SettingsResult` mirrors `RuleResult` shape (status/error/after). Audit-log API matches `mail/mutate/rules.py` (the G2 reference for the new pattern).

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-25-phase-9-mailbox-settings.md`.

Execution: subagent-driven-development. Branch `phase-9-mailbox-settings` already created off `main`.
