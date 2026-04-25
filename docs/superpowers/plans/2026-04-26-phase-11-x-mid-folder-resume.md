# Phase 11.x — Mid-Folder Export Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** When `mail export mailbox` is interrupted mid-folder, resuming should continue from the last successfully exported message instead of restarting the folder from scratch. Existing per-folder resume already skips folders marked `done`; Phase 11.x adds **mid-folder** continuation.

**Approach:**
- Track `last_exported_id` and `last_exported_received_at` per folder in the manifest.
- The mbox writer opens in append mode (`"ab"`) when resuming.
- The folder export iterates messages ordered `receivedDateTime asc` (already does this), skips messages until `received_at > last_exported_received_at` OR `message_id == last_exported_id` then takes the next one.
- Manifest is checkpointed after every message exported (cheap; the manifest is a small JSON file).

**Skip semantics:** Use `received_at > last_exported_received_at` as the primary skip rule because it's stable across the Graph endpoint. Fall back to `message_id == last_exported_id` as a tie-breaker for messages with identical timestamps. Documented in CHANGELOG: in the rare case where Outlook backfills a message with an older timestamp during the pause, that message is skipped — operator can re-run `export folder <path>` to capture it.

**Tech stack:** Existing primitives. No new deps.

**Baseline:** `main` post-PR-#24 (ca1f58c), 912 passing tests, 0 mypy errors, ruff clean. Tag `v1.7.0`.

**Version bump:** 1.7.0 → 1.8.0.

---

## File Structure

**Modify:**
- `src/m365ctl/mail/export/manifest.py` — add `last_exported_id: str | None = None`, `last_exported_received_at: str | None = None` to `FolderEntry`. Bump manifest schema to `version=2`. Read v1 manifests as v2 with the two new fields defaulting to None.
- `src/m365ctl/mail/export/mbox.py` — `MboxWriter` accepts `mode: Literal["w", "a"] = "w"` (default "w" for fresh starts; "a" for resume). `export_folder_to_mbox` accepts optional `resume_after: tuple[str, str] | None = None` (received_at_iso, message_id) — when set, opens the mbox in append mode AND skips messages until the cursor advances. Returns `(count, last_exported_id, last_exported_received_at)` instead of just `count`. Optional `progress_callback: Callable[[str, str], None] | None` invoked after each successful message write so the orchestrator can checkpoint the manifest.
- `src/m365ctl/mail/export/mailbox.py` — `export_mailbox` reads `last_exported_id` / `last_exported_received_at` from any existing in-progress entry and passes them to `export_folder_to_mbox`. Provides a `progress_callback` that updates the manifest entry after every message.
- Tests for all of the above.

---

## Group 1 — Manifest schema v2 (one commit)

**Files:**
- Modify: `src/m365ctl/mail/export/manifest.py`
- Modify: `tests/test_mail_export_manifest.py`

### Steps

- [ ] **Step 1: Failing tests** in `tests/test_mail_export_manifest.py`:
  - `FolderEntry` accepts `last_exported_id` and `last_exported_received_at` kwargs (default None).
  - `update_folder` accepts and stores both new fields (passed as kwargs).
  - Manifest write/read round-trips both fields.
  - Reading a v1 manifest (no `last_exported_*` fields, `version=1`) returns a Manifest where folder entries have both fields set to None — backward compatibility preserved.
  - `CURRENT_MANIFEST_VERSION = 2`.

- [ ] **Step 2:** Implement.

In `FolderEntry`:
```python
@dataclass
class FolderEntry:
    folder_id: str
    folder_path: str
    mbox_path: str
    status: str = "pending"
    count: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    last_exported_id: str | None = None
    last_exported_received_at: str | None = None
```

`Manifest.update_folder` signature gets two new kwargs (defaulting to `None` so callers don't have to pass them on every checkpoint):
```python
def update_folder(
    self, folder_id: str, *,
    folder_path: str, mbox_path: str,
    status: str, count: int,
    last_exported_id: str | None = None,
    last_exported_received_at: str | None = None,
) -> None:
    ...
    if last_exported_id is not None:
        existing.last_exported_id = last_exported_id
    if last_exported_received_at is not None:
        existing.last_exported_received_at = last_exported_received_at
```

Bump `CURRENT_MANIFEST_VERSION = 2`.

In `read_manifest`: if `raw["version"] == 1`, accept it and load into v2 `FolderEntry`s with the new fields defaulting to None. If `raw["version"] == 2`, load directly. If neither, raise `ManifestError`.

```python
def read_manifest(path: Path) -> Manifest:
    if not path.exists():
        return Manifest()
    raw = json.loads(path.read_text())  # ... existing error handling ...
    if raw.get("version") not in (1, 2):
        raise ManifestError(f"unsupported manifest version {raw.get('version')!r}")

    folders: dict[str, FolderEntry] = {}
    for fid, fe in (raw.get("folders") or {}).items():
        # v1 entries don't have last_exported_*; FolderEntry defaults to None.
        folders[fid] = FolderEntry(**fe)
    return Manifest(
        version=CURRENT_MANIFEST_VERSION,   # always write v2 going forward
        ...
    )
```

- [ ] **Step 3:** Quality gates: pytest (912 + ~5 = ~917), mypy 0, ruff clean.

- [ ] **Step 4: Commit:**
```
git add src/m365ctl/mail/export/manifest.py tests/test_mail_export_manifest.py
git commit -m "feat(mail/export/manifest): schema v2 — last_exported_id + last_exported_received_at"
```

---

## Group 2 — `export_folder_to_mbox` resume support (one commit)

**Files:**
- Modify: `src/m365ctl/mail/export/mbox.py`
- Modify: `tests/test_mail_export_mbox.py`

### Steps

- [ ] **Step 1: Failing tests** in `tests/test_mail_export_mbox.py`:

```python
def test_resume_after_skips_already_exported(tmp_path):
    """When resuming, messages with received_at <= cursor are skipped."""
    graph = MagicMock()
    graph.get_paginated.return_value = iter([(
        [
            {"id": "m1", "from": {"emailAddress": {"address": "a@example.com"}},
             "receivedDateTime": "2026-04-01T10:00:00Z"},
            {"id": "m2", "from": {"emailAddress": {"address": "b@example.com"}},
             "receivedDateTime": "2026-04-02T10:00:00Z"},
            {"id": "m3", "from": {"emailAddress": {"address": "c@example.com"}},
             "receivedDateTime": "2026-04-03T10:00:00Z"},
        ],
        None,
    )])
    graph.get_bytes.return_value = b"From: x\r\nSubject: y\r\n\r\nbody\r\n"

    out = tmp_path / "Inbox.mbox"
    # Pre-existing mbox content (already-exported m1, m2 simulated).
    out.write_bytes(b"From a@example.com Wed Apr  1 10:00:00 2026\nFrom: x\r\nSubject: y\r\n\r\nbody\r\n\n")

    count, last_id, last_ts = export_folder_to_mbox(
        graph, mailbox_spec="me", auth_mode="delegated",
        folder_id="fld-inbox", folder_path="Inbox", out_path=out,
        resume_after=("2026-04-02T10:00:00+00:00", "m2"),
    )
    # Only m3 is new (received after the cursor).
    assert count == 1
    assert last_id == "m3"
    # File appended to, not truncated; original prefix still present.
    assert out.read_bytes().startswith(b"From a@example.com")


def test_progress_callback_invoked_per_message(tmp_path):
    graph = MagicMock()
    msgs = [
        {"id": f"m{i}", "from": {"emailAddress": {"address": "a@example.com"}},
         "receivedDateTime": f"2026-04-0{i}T10:00:00Z"}
        for i in range(1, 4)
    ]
    graph.get_paginated.return_value = iter([(msgs, None)])
    graph.get_bytes.return_value = b"From: x\r\n\r\nbody\r\n"

    progress: list[tuple[str, str]] = []
    out = tmp_path / "Inbox.mbox"
    export_folder_to_mbox(
        graph, mailbox_spec="me", auth_mode="delegated",
        folder_id="f1", folder_path="Inbox", out_path=out,
        progress_callback=lambda mid, ts: progress.append((mid, ts)),
    )
    assert [m for m, _ in progress] == ["m1", "m2", "m3"]


def test_returns_last_id_and_received_at(tmp_path):
    graph = MagicMock()
    msgs = [
        {"id": "m1", "from": {"emailAddress": {"address": "a@example.com"}},
         "receivedDateTime": "2026-04-01T10:00:00Z"},
        {"id": "m2", "from": {"emailAddress": {"address": "b@example.com"}},
         "receivedDateTime": "2026-04-02T10:00:00Z"},
    ]
    graph.get_paginated.return_value = iter([(msgs, None)])
    graph.get_bytes.return_value = b"x"

    out = tmp_path / "Inbox.mbox"
    count, last_id, last_ts = export_folder_to_mbox(
        graph, mailbox_spec="me", auth_mode="delegated",
        folder_id="f1", folder_path="Inbox", out_path=out,
    )
    assert count == 2
    assert last_id == "m2"
    assert last_ts.startswith("2026-04-02")


def test_empty_folder_returns_none_for_cursor(tmp_path):
    graph = MagicMock()
    graph.get_paginated.return_value = iter([([], None)])
    out = tmp_path / "f.mbox"
    count, last_id, last_ts = export_folder_to_mbox(
        graph, mailbox_spec="me", auth_mode="delegated",
        folder_id="f", folder_path="X", out_path=out,
    )
    assert count == 0
    assert last_id is None
    assert last_ts is None
```

- [ ] **Step 2:** Implement.

In `MboxWriter`:
```python
class MboxWriter:
    def __init__(self, path: Path, *, mode: Literal["w", "a"] = "w"):
        self.path = path
        self._mode = mode
        self._fh: BinaryIO | None = None

    def __enter__(self) -> "MboxWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # "ab" for append, "wb" for fresh.
        flag = "ab" if self._mode == "a" else "wb"
        self._fh = open(self.path, flag)
        return self
    # __exit__ unchanged
```

In `export_folder_to_mbox`:
```python
def export_folder_to_mbox(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    folder_id: str,
    folder_path: str,
    out_path: Path,
    page_size: int = 100,
    resume_after: tuple[str, str] | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
) -> tuple[int, str | None, str | None]:
    """Returns (count, last_exported_id, last_exported_received_at)."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    list_path = f"{ub}/mailFolders/{folder_id}/messages"
    params = {
        "$select": "id,from,receivedDateTime,subject",
        "$orderby": "receivedDateTime asc",
        "$top": page_size,
    }

    cursor_ts, cursor_id = (resume_after if resume_after else (None, None))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not resume_after:
        out_path.touch()    # ensure fresh empty file

    mode: Literal["w", "a"] = "a" if resume_after else "w"
    count = 0
    last_id: str | None = None
    last_ts: str | None = None
    with MboxWriter(out_path, mode=mode) as writer:
        for items, _ in graph.get_paginated(list_path, params=params):
            for raw in items:
                mid = raw["id"]
                received_str = raw.get("receivedDateTime") or ""
                if cursor_ts is not None:
                    # Skip messages at or before the cursor.
                    if received_str <= cursor_ts and mid != cursor_id:
                        continue
                    if mid == cursor_id:
                        # The cursor message itself is already exported;
                        # skip it and clear cursor so subsequent messages
                        # (which all have received_at >= cursor_ts) export.
                        cursor_ts = None
                        continue
                    # Past the cursor — clear it so we stop comparing.
                    cursor_ts = None

                sender = (raw.get("from") or {}).get("emailAddress", {}).get("address") or "unknown"
                received = _parse_iso(received_str)
                eml = fetch_eml_bytes(
                    graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
                    message_id=mid,
                )
                writer.append(eml, sender_addr=sender, received_at=received)
                count += 1
                last_id = mid
                last_ts = received_str
                if progress_callback is not None:
                    progress_callback(mid, received_str)
    return count, last_id, last_ts
```

Note on the skip logic: ISO-8601 timestamp string comparison works for cursor checks because the format is lexicographically sortable when consistent. The `received_str <= cursor_ts and mid != cursor_id` keeps messages strictly before the cursor out, but the explicit `mid == cursor_id` branch handles the exact-cursor message safely.

- [ ] **Step 3:** Quality gates: pytest (917 + ~4 = ~921), mypy 0, ruff clean.

- [ ] **Step 4: Commit:**
```
git add src/m365ctl/mail/export/mbox.py tests/test_mail_export_mbox.py
git commit -m "feat(mail/export/mbox): mid-folder resume — append mode + cursor-skip + progress callback"
```

---

## Group 3 — `export_mailbox` orchestrator wiring (one commit)

**Files:**
- Modify: `src/m365ctl/mail/export/mailbox.py`
- Modify: `tests/test_mail_export_mailbox.py`

### Steps

- [ ] **Step 1: Failing tests** in `tests/test_mail_export_mailbox.py`:

- Resuming `export_mailbox` against an existing manifest with a folder marked `in_progress` and `last_exported_id=...` passes the `(last_received_at, last_id)` cursor to `export_folder_to_mbox` and uses append mode (mock `export_folder_to_mbox`, assert kwargs).
- Folder marked `pending` (or new, never started) calls `export_folder_to_mbox` with no `resume_after` (fresh start).
- Folder marked `done` is skipped entirely (existing behavior — verify it still passes).
- Per-message progress is captured in the manifest after each callback (use a `progress_callback` mock that calls `manifest.update_folder` directly).
- After the folder completes, manifest entry is `status="done"` and `last_exported_id` matches the final message.

- [ ] **Step 2:** Implement. Update `export_mailbox` in `mail/export/mailbox.py`:

```python
def export_mailbox(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    mailbox_upn: str,
    auth_mode: AuthMode,
    out_dir: Path,
) -> Manifest:
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
        existing = manifest.folders.get(folder.id)
        # Resume cursor if the folder was in_progress with a last-exported id.
        resume_after: tuple[str, str] | None = None
        if (existing and existing.status == "in_progress"
                and existing.last_exported_id and existing.last_exported_received_at):
            resume_after = (existing.last_exported_received_at, existing.last_exported_id)

        safe = _sanitise(folder.path)
        mbox_path = out_dir / f"{safe}.mbox"
        manifest.update_folder(
            folder.id,
            folder_path=folder.path,
            mbox_path=str(mbox_path.relative_to(out_dir)),
            status="in_progress",
            count=existing.count if existing else 0,
        )
        write_manifest(manifest, manifest_path)

        # Per-message checkpoint callback.
        def _checkpoint(mid: str, ts: str, *, _fid=folder.id, _fp=folder.path,
                        _rel=str(mbox_path.relative_to(out_dir))) -> None:
            entry = manifest.folders[_fid]
            manifest.update_folder(
                _fid,
                folder_path=_fp, mbox_path=_rel,
                status="in_progress", count=entry.count + 1,
                last_exported_id=mid,
                last_exported_received_at=ts,
            )
            write_manifest(manifest, manifest_path)

        try:
            count, last_id, last_ts = export_folder_to_mbox(
                graph,
                mailbox_spec=mailbox_spec,
                auth_mode=auth_mode,
                folder_id=folder.id,
                folder_path=folder.path,
                out_path=mbox_path,
                resume_after=resume_after,
                progress_callback=_checkpoint,
            )
        except Exception:
            write_manifest(manifest, manifest_path)
            raise

        manifest.update_folder(
            folder.id,
            folder_path=folder.path,
            mbox_path=str(mbox_path.relative_to(out_dir)),
            status="done",
            count=manifest.folders[folder.id].count,  # callback already advanced it
            last_exported_id=last_id,
            last_exported_received_at=last_ts,
        )
        write_manifest(manifest, manifest_path)
    return manifest
```

The `_checkpoint` closure captures defaults via kwargs to avoid the late-binding-loop-variable Python gotcha.

- [ ] **Step 3:** Quality gates: pytest (921 + ~5 = ~926), mypy 0, ruff clean.

- [ ] **Step 4: Commit:**
```
git add src/m365ctl/mail/export/mailbox.py tests/test_mail_export_mailbox.py
git commit -m "feat(mail/export/mailbox): wire mid-folder resume via cursor + per-message manifest checkpoint"
```

---

## Group 4 — Release 1.8.0

### Task 4.1: Bump + changelog + README + lockfile (2 commits)

- [ ] `pyproject.toml`: 1.7.0 → 1.8.0.

- [ ] Prepend CHANGELOG.md:

```markdown
## 1.8.0 — Phase 11.x: mid-folder export resume

### Added
- `mail export mailbox` now resumes interrupted folders mid-stream.
  After each successfully exported message the manifest is checkpointed
  with `last_exported_id` + `last_exported_received_at`; the next run
  opens the same `.mbox` in append mode and skips messages at or before
  the cursor.
- Manifest schema bumped to v2 (additive — `last_exported_id` and
  `last_exported_received_at` per folder). v1 manifests load
  transparently with the new fields defaulting to None.

### Skip semantics
Cursor comparison is `received_at > last_exported_received_at`, with
`message_id == last_exported_id` as the exact-match tie-breaker. ISO-8601
strings sort lexicographically so the comparison is exact. **Caveat:**
if Outlook backfills a message with an older `receivedDateTime` during
the pause, that message is skipped — re-run `mail export folder <path>`
to capture it.

### Contract change
`export_folder_to_mbox` returns `(count, last_id, last_ts)` instead of
just `count`. CLI callers in `mail export folder` continue to work
(they only use the count).
```

- [ ] README Mail bullet:
```markdown
- **Mid-folder export resume (Phase 11.x, 1.8):** `mail export mailbox`
  now resumes interrupted folders message-by-message via
  `last_exported_id` checkpoints in the manifest. Killing mid-export
  and re-running picks up where it left off; no re-uploads.
```

- [ ] `uv sync --all-extras`. Quality gates. Two release commits per the no-amend rule.

### Task 4.2: Push, PR, merge, tag v1.8.0

Standard cadence.

---

## Self-review

**Spec coverage:**
- ✅ Mid-folder resume (the only Phase 11.x deferral).
- ✅ Manifest schema upgrade with backward compat for v1 manifests.

**Backwards compat:**
- `export_folder_to_mbox` return type changes — but it has only one external caller (`cli/export.py:_run_folder_export`). Update that caller to unpack `(count, _, _)`.
- `MboxWriter.__init__` adds `mode` as keyword-only with default `"w"` — existing call sites unchanged.
- v1 manifest files load as v2 (the existing `read_manifest` accepts both versions); next save writes as v2.

**Type consistency:** `tuple[str, str] | None` for `resume_after` is unambiguous; keyword-only `progress_callback` is `Callable[[str, str], None] | None`.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-26-phase-11-x-mid-folder-resume.md`. Branch `phase-11-x-resume` already off `main`.
