# Phase 14 — Convenience Commands → 1.0.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan group-by-group. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Six daily-driver convenience verbs composed on top of the already-shipped primitives. Phase 14 ships as **v1.0.0** — the "complete" milestone.

**Architecture:**
- All six verbs live under `m365ctl.mail.convenience.<verb>` modules. Each is a thin orchestrator that calls existing functions in `mail.messages`, `mail.catalog.queries`, `mail.mutate.*`, `mail.compose.send`, etc.
- New CLI subcommands routed via top-level `m365ctl mail <verb>` dispatcher entries (and bin wrappers).
- No new mutators, no new audit-action namespaces — every state change goes through existing verbs (`mail.move`, `mail.categorize`, `mail.send.inline`).
- `digest` builds a markdown/HTML body and either prints it (default) or composes-and-sends to `--send-to`.
- `archive` resolves "messages in <folder> older than <N>d", emits a single bulk-move plan tagged with rule-name `mail-archive-<YYYYMM>`. User confirms; existing `mail.move` execution path runs it.
- `unsubscribe` fetches the message with `internetMessageHeaders`, parses `List-Unsubscribe` (RFC 8058 + RFC 2369), prints discovered methods, optionally opens the `mailto:` link or hits the HTTPS one.
- `snooze` moves a message into `Deferred/<YYYY-MM-DD>` and tags it `Snooze/<date>`. `--process` walks `Deferred/<today-or-earlier>` folders and moves messages back to inbox.
- `top-senders` and `size-report` are catalog-only readers that emit human-readable / JSON output.

**Tech stack:** Existing primitives. No new deps.

**Baseline:** `main` post-PR-#16 (eaf6243), 747 passing tests, 0 mypy errors. Tag `v0.11.0` shipped.

**Version bump:** 0.11.0 → 1.0.0.

---

## File Structure

**New:**
- `src/m365ctl/mail/convenience/__init__.py` — empty.
- `src/m365ctl/mail/convenience/digest.py` — `build_digest`, `render_text`, `render_html`.
- `src/m365ctl/mail/convenience/archive.py` — `build_archive_plan` (returns `Plan` of `mail.move` ops).
- `src/m365ctl/mail/convenience/unsubscribe.py` — `parse_list_unsubscribe`, `discover_methods`.
- `src/m365ctl/mail/convenience/snooze.py` — `build_snooze_op`, `find_due_snoozed`.
- `src/m365ctl/mail/convenience/top_senders.py` — wraps `catalog.queries.top_senders` with `--since` filter.
- `src/m365ctl/mail/convenience/size_report.py` — wraps `catalog.queries.size_per_folder`.
- `src/m365ctl/mail/cli/digest.py`, `cli/archive.py`, `cli/unsubscribe.py`, `cli/snooze.py`, `cli/top_senders.py`, `cli/size_report.py`.
- `bin/mail-digest`, `bin/mail-archive`, `bin/mail-unsubscribe`, `bin/mail-snooze`, `bin/mail-top-senders`, `bin/mail-size-report`.
- Tests: `tests/test_mail_convenience_<verb>.py` and `tests/test_cli_mail_<verb>.py` per verb.
- `docs/mail/convenience-commands.md` — user-facing docs with example output.

**Modify:**
- `src/m365ctl/mail/cli/__main__.py` — route six new verbs + `_USAGE` block.
- `pyproject.toml` — bump 0.11.0 → 1.0.0.
- `CHANGELOG.md` — 1.0.0 section + retrospective on the journey.
- `README.md` — Mail bullet + bump version badge if present.

---

## Group 1 — Digest (read + optional send)

**Files:**
- Create: `src/m365ctl/mail/convenience/__init__.py`, `digest.py`
- Create: `src/m365ctl/mail/cli/digest.py`
- Create: `bin/mail-digest`
- Create: `tests/test_mail_convenience_digest.py`, `tests/test_cli_mail_digest.py`

**Behaviour:**
- Default: query unread messages from inbox via the catalog (case `--since 24h`), render a structured summary, print to stdout.
- `--send-to me` (or `<addr>`): compose an HTML email with the digest body and send via `mail.compose.send_inline`. Subject line: `[Digest] N unread since <since>`.
- `--since` accepts `<N>h`, `<N>d`, or an ISO timestamp.
- Sections of the digest: top senders by count, unread by category (Work/Triage/etc.), and a flat list of newest-N (default 20).

### Task 1.1: Pure-logic digest builder

- [ ] Tests at `tests/test_mail_convenience_digest.py`:
  - `parse_since("24h")` → `timedelta(hours=24)`.
  - `parse_since("3d")` → `timedelta(days=3)`.
  - `parse_since("2026-04-20T00:00:00Z")` → corresponding `datetime`.
  - `parse_since("garbage")` raises `DigestError`.
  - `build_digest(rows, since, now)` → `Digest` dataclass with `top_senders`, `by_category`, `recent` populated correctly. Use a small fixture with 5 rows.
  - `render_text(digest)` produces a multi-section plain-text summary including sender counts and message subjects.
  - `render_html(digest)` produces HTML with `<h2>` section headings and a `<ul>` of recent messages.

- [ ] Implement `src/m365ctl/mail/convenience/digest.py`:

```python
"""Build an unread-mail digest from catalog rows.

Pure data → text/HTML transformation. No Graph calls inside this module
(the CLI fetches catalog rows and feeds them in).

Sections:
  1. Top senders by count (desc), capped at top 10.
  2. By category (categories field, comma-joined → buckets).
  3. Recent (newest by received_at, capped at limit).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


class DigestError(ValueError):
    pass


@dataclass(frozen=True)
class DigestEntry:
    message_id: str
    subject: str
    from_address: str
    received_at: datetime
    categories: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Digest:
    since: datetime
    now: datetime
    total: int
    top_senders: list[tuple[str, int]]
    by_category: dict[str, int]
    recent: list[DigestEntry]


def parse_since(s: str, *, now: datetime | None = None) -> datetime:
    """Convert ``24h`` / ``3d`` / ISO string into an absolute datetime."""
    if now is None:
        now = datetime.now(timezone.utc)
    s = s.strip()
    if not s:
        raise DigestError("--since cannot be empty")
    if s[-1] in ("h", "d") and s[:-1].isdigit():
        n = int(s[:-1])
        if s.endswith("h"):
            return now - timedelta(hours=n)
        return now - timedelta(days=n)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as e:
        raise DigestError(f"--since {s!r} is neither a duration ({{N}}h|{{N}}d) "
                          f"nor an ISO-8601 datetime") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def build_digest(
    rows: list[dict[str, Any]],
    *,
    since: datetime,
    now: datetime,
    limit: int = 20,
) -> Digest:
    filtered = [r for r in rows if _received_after(r, since)]
    sender_counts = Counter(r.get("from_address") or "(unknown)" for r in filtered)
    by_category: dict[str, int] = Counter()
    for r in filtered:
        cats = (r.get("categories") or "").split(",")
        for c in cats:
            c = c.strip()
            if c:
                by_category[c] += 1
        if not any(c.strip() for c in cats):
            by_category["(uncategorised)"] += 1
    recent_rows = sorted(filtered,
                         key=lambda r: r.get("received_at") or datetime.min,
                         reverse=True)[:limit]
    recent = [
        DigestEntry(
            message_id=r["message_id"],
            subject=r.get("subject") or "",
            from_address=r.get("from_address") or "",
            received_at=_to_dt(r.get("received_at")),
            categories=[c.strip() for c in (r.get("categories") or "").split(",") if c.strip()],
        )
        for r in recent_rows
    ]
    return Digest(
        since=since,
        now=now,
        total=len(filtered),
        top_senders=sender_counts.most_common(10),
        by_category=dict(by_category),
        recent=recent,
    )


def _received_after(row: dict, since: datetime) -> bool:
    received = row.get("received_at")
    if received is None:
        return False
    received = _to_dt(received)
    return received >= since


def _to_dt(received) -> datetime:
    if isinstance(received, datetime):
        if received.tzinfo is None:
            return received.replace(tzinfo=timezone.utc)
        return received
    if isinstance(received, str):
        d = datetime.fromisoformat(received.replace("Z", "+00:00"))
        if d.tzinfo is None:
            return d.replace(tzinfo=timezone.utc)
        return d
    return datetime.min.replace(tzinfo=timezone.utc)


def render_text(d: Digest) -> str:
    lines = [
        f"Mail digest — since {d.since.isoformat(timespec='minutes')} (now {d.now.isoformat(timespec='minutes')})",
        f"Total: {d.total} unread",
        "",
        "Top senders:",
    ]
    for addr, n in d.top_senders:
        lines.append(f"  {n:>4} {addr}")
    lines += ["", "By category:"]
    for cat, n in sorted(d.by_category.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {n:>4} {cat}")
    lines += ["", f"Recent ({len(d.recent)}):"]
    for e in d.recent:
        ts = e.received_at.isoformat(timespec="minutes")
        lines.append(f"  {ts}  {e.from_address:<32}  {e.subject}")
    return "\n".join(lines) + "\n"


def render_html(d: Digest) -> str:
    parts = [
        f"<h2>Mail digest — since {d.since.isoformat(timespec='minutes')}</h2>",
        f"<p><strong>Total:</strong> {d.total} unread</p>",
        "<h3>Top senders</h3><ul>",
        *(f"<li>{n} — {addr}</li>" for addr, n in d.top_senders),
        "</ul>",
        "<h3>By category</h3><ul>",
        *(f"<li>{n} — {cat}</li>" for cat, n in sorted(d.by_category.items(), key=lambda kv: -kv[1])),
        "</ul>",
        f"<h3>Recent ({len(d.recent)})</h3><ul>",
    ]
    for e in d.recent:
        ts = e.received_at.isoformat(timespec="minutes")
        parts.append(f"<li>{ts} — <strong>{e.from_address}</strong>: {e.subject}</li>")
    parts.append("</ul>")
    return "\n".join(parts)
```

- [ ] Run tests, mypy + ruff clean. Commit:
```
feat(mail/convenience): digest builder + parse_since + text/HTML renderers
```

### Task 1.2: CLI

- [ ] Tests at `tests/test_cli_mail_digest.py`:
  - `mail digest` (no flags) loads catalog, builds digest, prints text.
  - `mail digest --since 3d --json` prints NDJSON.
  - `mail digest --send-to me --confirm` calls `mail.compose.send_inline` with the HTML body and self-recipient.
  - `mail digest --send-to me` without `--confirm` returns 0 with a dry-run notice on stderr.

- [ ] Implement `src/m365ctl/mail/cli/digest.py`. Read catalog rows via:
  ```python
  with open_catalog(cfg.mail.catalog_path) as conn:
      rows = conn.execute(
          "SELECT message_id, subject, from_address, received_at, "
          "categories FROM mail_messages WHERE mailbox_upn = ? "
          "AND is_read = false AND is_deleted = false",
          [mailbox_upn],
      ).fetchall()
      cols = [d[0] for d in conn.execute("...").description]
      # ... materialise as list[dict]
  ```
  (Or use a small helper in `digest.py` that takes a connection.)
- For send-mode, call into the existing send-inline executor with `subject="[Digest] N unread since X"`, `body=render_html(d)`, `body_type="html"`, `to=[args.send_to]`.

- [ ] Wire dispatcher route + bin wrapper. Commit:
```
feat(mail/cli): mail digest --since|--send-to|--limit + bin wrapper
```

---

## Group 2 — Archive + size report (read-mostly)

**Files:**
- Create: `convenience/archive.py`, `convenience/size_report.py`
- Create: `cli/archive.py`, `cli/size_report.py`
- Create: bin wrappers
- Tests per file

### Task 2.1: archive — emit bulk-move plan

`mail archive --older-than 90d --folder Inbox [--plan-out plan.json | --confirm]`

The archive convenience emits a `Plan` containing one `mail.move` op per qualifying message. Destination is `Archive/<YYYY>/<MM>` based on each message's `received_at`. With `--plan-out` it writes the plan and exits (dry run). With `--confirm` it writes a temp plan, then dispatches via the existing per-action executors (mirror Phase 10's runner pattern — same `_EXECUTORS` table, same dispatcher).

- [ ] Tests at `tests/test_mail_convenience_archive.py`:
  - `build_archive_plan(rows, *, older_than_days, folder, mailbox_upn, now)` emits one `mail.move` op per message with `to_folder=Archive/<YYYY>/<MM>` derived from each row's `received_at`. Messages newer than the cutoff are excluded.
  - Operations carry `args["rule_name"] == f"mail-archive-{YYYY}{MM}"`.
  - Plan metadata: `source_cmd` includes the CLI invocation and `scope=mailbox_spec`.
  - Empty input → empty plan.

- [ ] Tests at `tests/test_cli_mail_archive.py`:
  - `mail archive --older-than 90d --folder Inbox --plan-out plan.json` writes the plan to disk and exits 0.
  - `mail archive --older-than 90d --folder Inbox` (no plan-out, no confirm) returns 2 with a clear stderr.
  - With `--confirm` and a non-empty plan, calls into the existing executor table once per op.

- [ ] Implement `convenience/archive.py`:

```python
"""Build a bulk-move plan that lands old messages into Archive/<YYYY>/<MM>."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from m365ctl.common.planfile import PLAN_SCHEMA_VERSION, Operation, Plan, new_op_id


def build_archive_plan(
    rows: list[dict[str, Any]],
    *,
    older_than_days: int,
    folder: str,
    mailbox_upn: str,
    source_cmd: str,
    scope: str,
    now: datetime,
) -> Plan:
    cutoff = now - timedelta(days=older_than_days)
    ops: list[Operation] = []
    for r in rows:
        path = r.get("parent_folder_path") or ""
        if path != folder:
            continue
        received = r.get("received_at")
        if received is None:
            continue
        if isinstance(received, str):
            received = datetime.fromisoformat(received.replace("Z", "+00:00"))
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        if received >= cutoff:
            continue
        rule_name = f"mail-archive-{received:%Y%m}"
        target = f"Archive/{received:%Y}/{received:%m}"
        ops.append(Operation(
            op_id=new_op_id(),
            action="mail.move",
            drive_id=mailbox_upn,
            item_id=r["message_id"],
            args={"rule_name": rule_name, "to_folder": target},
            dry_run_result=f"[{rule_name}] would move → {target}: "
                           f"{r.get('from_address')} | {r.get('subject', '')[:60]}",
        ))
    return Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at=now.isoformat(),
        source_cmd=source_cmd,
        scope=scope,
        operations=ops,
    )
```

- [ ] Implement `cli/archive.py`. Reuse Phase 10's runner `_EXECUTORS` table (import `from m365ctl.mail.triage.runner import _EXECUTORS`) for the `--confirm` execute path.

- [ ] Wire dispatcher route + bin wrapper. Commit:
```
feat(mail/convenience): archive — bulk-move-by-month plan
feat(mail/cli): mail archive --older-than|--folder|--plan-out|--confirm
```
(Two commits if you want, one combined is fine too.)

### Task 2.2: size-report — catalog-driven folder breakdown

`mail size-report [--top N] [--json]`

- [ ] Tests covering: report orders folders by total `size_estimate` desc, includes message_count + total_size; `--top 5` truncates; `--json` emits NDJSON.
- [ ] Implement `convenience/size_report.py` (calls `catalog.queries.size_per_folder`).
- [ ] Implement `cli/size_report.py`. Wire dispatcher + bin wrapper. Commit:
```
feat(mail/cli): mail size-report — catalog-driven folder size breakdown
```

---

## Group 3 — Top senders + Unsubscribe + Snooze

### Task 3.1: top-senders

`mail top-senders [--since 30d] [--limit 20] [--json]`

- Wraps `catalog.queries.top_senders` with a `--since` filter. The catalog query already supports `mailbox_upn`; the `--since` filter is applied client-side in the convenience module (catalog already filters out tombstones).

- [ ] Tests + impl + CLI. One commit.

### Task 3.2: unsubscribe

`mail unsubscribe <message-id> [--method http|mailto|first] [--dry-run]`

Behaviour:
1. Fetch message with `?$select=internetMessageHeaders`.
2. Find `List-Unsubscribe` header.
3. Parse out `mailto:` and `https://` URLs.
4. By default print discovered methods + dry-run summary.
5. With `--method http`: if a URL exists, hit it via `httpx.get(...)` (one-shot, log status).
6. With `--method mailto`: print the address + suggested subject (don't auto-send).
7. With `--method first`: prefer http if available, else mailto.
8. Document RFC 8058 (`List-Unsubscribe-Post: List-Unsubscribe=One-Click`) detection — if present and `--method http`, send a POST instead of GET.

- [ ] Tests at `tests/test_mail_convenience_unsubscribe.py`:
  - `parse_list_unsubscribe("<mailto:x@y>, <https://z>")` → list of typed methods.
  - Multiple URLs: returns all.
  - Empty header → empty list.
  - Malformed: discards bad entries, keeps good ones.
  - `discover_methods(message_dict)` extracts headers from `internetMessageHeaders` and parses them.

- [ ] Tests at `tests/test_cli_mail_unsubscribe.py`:
  - Default (no `--method`) prints discovered URLs + exits 0.
  - `--method http --dry-run` prints what it would do.
  - `--method http --confirm` calls `httpx.get` (mock).
  - Message with no `List-Unsubscribe` header → returns 0 with stderr "(no unsubscribe header)".

- [ ] Implement, dispatch, bin wrapper. Commit:
```
feat(mail/convenience): unsubscribe — RFC 2369/8058 List-Unsubscribe parser + http/mailto dispatcher
```

### Task 3.3: snooze

`mail snooze <message-id> --until <ISO-or-relative> --confirm`
`mail snooze --process [--confirm]`

Behaviour:
- `--until 2026-05-01` (or `5d`/`24h`-style relative): create folder `Deferred/2026-05-01` if missing, move the message there, add category `Snooze/2026-05-01`.
- `--process`: enumerate folders matching `Deferred/<YYYY-MM-DD>`. For each whose date is today-or-earlier, move all its messages back to Inbox and remove the matching `Snooze/<date>` category.

This is the only convenience verb that does writes outside of `mail.move`. We add no new audit-action namespaces — it composes existing `mail.move` + `mail.categorize` ops. The opinionated `Deferred/<date>` choice is documented in the spec §20 as "convenience, not core".

- [ ] Tests at `tests/test_mail_convenience_snooze.py`:
  - `parse_until("2026-05-01")` → that date.
  - `parse_until("5d", now=...)` → date 5 days hence.
  - `parse_until("garbage")` raises.
  - `build_snooze_op(message_id, due_date, mailbox_upn)` returns a list of two `Operation`s: one move, one categorize-add.
  - `find_due_snoozed(folders, *, today)` returns the folder paths matching the convention with date ≤ today.

- [ ] CLI tests + implementation. Reuse Phase 10's executor table for the confirm-execute path. Commit:
```
feat(mail/convenience): snooze — Deferred/<date> + Snooze/<date> categorize, --process moves due back
```

---

## Group 4 — Docs + 1.0.0 release

### Task 4.1: User-facing docs

Create `docs/mail/convenience-commands.md` with one section per verb. Each section: synopsis, example invocation, generic example output (using `example.com` addresses). This satisfies spec deliverable "All documented with generic example output in `docs/`."

- [ ] One commit:
```
docs(mail): convenience-commands reference (digest|archive|unsubscribe|snooze|top-senders|size-report)
```

### Task 4.2: 1.0.0 retrospective changelog + bump

- [ ] `pyproject.toml`: 0.11.0 → 1.0.0.

- [ ] Prepend CHANGELOG.md:

```markdown
## 1.0.0 — Phase 14: convenience commands → "complete" milestone

m365ctl ships its first stable release.

### Added (Phase 14)
- `mail digest [--since|--send-to|--limit]` — unread digest builder with text/HTML rendering and optional self-mail.
- `mail archive --older-than|--folder|--plan-out|--confirm` — bulk-move plan into `Archive/<YYYY>/<MM>` with the existing audit/undo path.
- `mail size-report [--top|--json]` — catalog-driven folder size + count breakdown.
- `mail top-senders [--since|--limit|--json]` — catalog shortcut over `top_senders` query.
- `mail unsubscribe <id> [--method http|mailto|first]` — RFC 2369 / RFC 8058 List-Unsubscribe parser + dispatcher.
- `mail snooze <id> --until <date> --confirm` and `mail snooze --process` — `Deferred/<YYYY-MM-DD>` + `Snooze/<date>` category convention.
- `docs/mail/convenience-commands.md` — generic-example reference.

### What 1.0.0 covers
A complete CLI for Microsoft 365 OneDrive + SharePoint + Mail via Microsoft Graph:
- **OneDrive:** auth, catalog (DuckDB + /delta), inventory, search, move/copy/rename/delete (incl. recycle/restore/clean), label, audit-sharing, undo.
- **Mail readers:** auth, whoami, list, get, search, folders, categories, rules, settings, attach.
- **Mail mutators:** move, copy, flag, read, focus, categorize, soft-delete (with undo via rotated-id recovery), draft, send, reply, forward.
- **Mail catalog:** DuckDB mirror via `/delta` with per-folder `--max-rounds` cap.
- **Triage DSL:** YAML rules → match → tagged plan → confirm-execute, reusing all mutate executors.
- **Inbox rules CRUD:** server-side YAML round-trip with full audit/undo.
- **Mailbox settings:** OOO (60-day safety gate + `--force` bypass), signature (local-file fallback), timezone, working hours.
- **Export:** EML, streaming MBOX, attachments, full-mailbox manifest with resume-on-interrupt.
- **Convenience verbs:** digest / archive / unsubscribe / snooze / top-senders / size-report.

### Out of scope for 1.0
- Phase 5a-2 (chunked attach upload ≥3 MB), Phase 5b (scheduled send), Phase 6 (hard delete + `mail clean`), Phase 12 (multi-mailbox / delegation), Phase 13 (send-as / on-behalf-of). All sit in the backlog with their dependencies satisfied.
- KQL pushdown for the triage DSL (catalog covers the surface area we needed).
- Body / thread / headers predicates in the triage DSL.

### Compatibility
Python 3.11+, tested against Python 3.11/3.12/3.13 on ubuntu-latest and macos-latest.

### Quality gates
- mypy: 0 errors across the source tree (CI-blocking since 0.7.x).
- ruff: clean.
- pytest: ~785 passing (varies as suite grows), 1 live-Graph test gated behind `M365CTL_LIVE_TESTS=1`.
```

- [ ] README Mail bullet:
```markdown
- **Convenience verbs (Phase 14, 1.0):** `mail {digest, archive, snooze,
  unsubscribe, top-senders, size-report}` — daily-driver composition over
  the core surface. See `docs/mail/convenience-commands.md` for each one's
  synopsis and example output.
```

- [ ] `uv sync --all-extras`. Full quality gates. Two release commits per the no-amend rule.

### Task 4.3: Push, PR, merge, tag v1.0.0

Push branch, open PR titled `Phase 14: convenience commands → 1.0.0` with a comprehensive body covering the six verbs plus the 1.0.0 milestone retrospective. Watch CI, squash-merge, sync main, tag `v1.0.0`.

---

## Self-review

**Spec coverage (§19 Phase 14):**
- ✅ `mail-digest [--since 24h] [--send-to me]` — Group 1.
- ✅ `mail-archive --older-than 90d --folder Inbox` — Group 2.1, archives into `Archive/<YYYY>/<MM>` per spec.
- ✅ `mail-unsubscribe <message-id>` — Group 3.2.
- ✅ `mail-snooze <message-id> --until <iso>` and `--process` — Group 3.3.
- ✅ `mail-top-senders --since 30d --limit 20` — Group 3.1.
- ✅ `mail-size-report` — Group 2.2.
- ✅ Generic `example.com` documentation — Group 4.1.
- ✅ Bump to 1.0.0 — Group 4.2.

**Acceptance:**
- Every verb composes existing primitives only — no new audit-action namespaces, no new mutators, no new Graph endpoints beyond `internetMessageHeaders` for unsubscribe.
- `--confirm` gating on every mutating verb (digest send, archive, snooze, snooze --process, unsubscribe http/post). No accidental writes.

**Type consistency:** Every emitted `Operation` uses an existing `mail.*` action name. `Plan` schema unchanged.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-25-phase-14-convenience-1.0.0.md`. Branch `phase-14-convenience-1.0.0` already off `main`.
