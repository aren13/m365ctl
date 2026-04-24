# Recycle-bin Restore + Purge (OneDrive-for-Business) — Plan 4 follow-up

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `od-undo` on a `od-delete` op actually restore the file, and make `od-clean recycle-bin` actually purge it, against OneDrive-for-Business. Plan 4 landed with both verbs returning informative error messages pointing operators at manual workarounds. This plan replaces those workarounds with PnP.PowerShell shell-outs, the same integration pattern already proven by `od-label`.

**Why this exists:** Microsoft Graph v1.0 has no public endpoint for either operation against ODfB items:
- `POST /drives/{d}/items/{i}/restore` is documented **OneDrive-Personal only**; ODfB returns `notSupported`.
- `POST /drives/{d}/items/{i}/permanentDelete` targets **live** items only; recycle-bin items return 404.

The supported paths on macOS/Linux are SharePoint REST (auth complexity) or PnP.PowerShell (already set up for `od-label` and `od-audit-sharing`). We pick PnP because the prerequisites (pwsh + PFX + Keychain + SharePoint-API permission) are already documented in `docs/ops/pnp-powershell-setup.md` and verified during Plan 3 Task 12 Step 6.

**Architecture:** Two new PowerShell scripts under `scripts/ps/`, each reusing the existing cert + Keychain auth pattern. Two existing mutate functions (`execute_restore`, `purge_recycle_bin_item`) gain a subprocess shell-out path that runs *after* the Graph-API attempt fails with the known ODfB error tokens — so OneDrive-Personal keeps working natively and ODfB picks up PnP transparently. The audit log contract is unchanged: one `start`/`end` pair per op, `after` block carries the recycle-bin item id that was restored/purged.

**Tech stack:** Python 3.11+, `subprocess` (stdlib), `pwsh` + `PnP.PowerShell` ≥ 2.x (installed and verified per `docs/ops/pnp-powershell-setup.md`). No new Python dependencies.

**End-state (definition of done):**
- `./bin/od-delete … --confirm` followed by `./bin/od-undo <op_id> --confirm` round-trips a file through the recycle bin and back against ODfB. Audit log shows `start`/`end` for both ops with `result: ok`.
- `./bin/od-clean recycle-bin --from-plan … --confirm` permanently deletes a recycle-bin item. `od-undo` on that op raises `Irreversible` via the `cmd == "od-clean(recycle-bin)"` branch (not via the fallback "did not succeed originally" branch that we currently fall through to).
- Both PS scripts have unit tests (subprocess mocked, same pattern as `test_mutate_label.py`).
- Live smoke test round-trips one throwaway file (same `_m365ctl_smoke/hello.txt` shape as Plan 4 Task 13).
- `docs/ops/pnp-powershell-setup.md` updated with any new prereqs (likely none — `Sites.FullControl.All` already covers this).
- `AGENTS.md` unchanged on the verb-surface side (same `od-undo` / `od-clean`) but gains a short note in the Plan 4 "Mutation safety envelope" subsection that ODfB restore/purge now goes through PnP.

**Dependencies (already in place from Plans 1–4):**
- `m365ctl.graph.GraphClient` + `GraphError`.
- `m365ctl.audit.AuditLogger` + start/end helpers.
- `m365ctl.safety.assert_scope_allowed` (still called before the shell-out — PnP can't bypass scope).
- `m365ctl.planfile.Operation`.
- `m365ctl.mutate.delete.{execute_recycle_delete, execute_restore, DeleteResult}`.
- `m365ctl.mutate.clean.{purge_recycle_bin_item, CleanResult}`.
- PnP.PowerShell 3.x + PFX + Keychain entry `m365ctl:PfxPassword` / `m365ctl`.

**Intentionally deferred (not this plan):**
- Version history restore (`od-clean old-versions` has no undo; unrecoverable-by-design).
- Stale-share re-issue (`od-clean stale-shares` undo is impossible — link URLs are not reproducible).
- Batched recycle-bin operations (`Clear-PnPRecycleBinItem -All` or similar). Each op goes through the audit log separately; batching is a next-next step.
- Cross-tenant restore.
- OneDrive-Personal path (already works natively via Graph; we keep the Graph call as the primary, PS is fallback).

---

## Domain primer

### How PnP resolves a recycle-bin item

Recycle-bin items in ODfB/SharePoint have their **own GUIDs** that are *not* the same as the original `driveItem.id`. Listing: `Get-PnPRecycleBinItem` returns objects with:
- `Id` (recycle-bin-item GUID; unique per site recycle bin)
- `LeafName` (filename at delete time)
- `DirName` (original parent path, site-relative)
- `ItemType` (`File` | `Folder`)
- `DeletedDate`

Matching strategy: we record the file's `name` and `parent_path` in the audit log's `before` block at delete time. On restore, we pass those two fields to the PS script; the script enumerates `Get-PnPRecycleBinItem`, filters by `LeafName == $Name -and $DirName -like "*$ParentPath"`, sorts by `DeletedDate` descending, takes the most recent. If zero matches, error; if multiple ambiguous matches, pick the newest and log a warning (the recent-most delete is almost certainly the one the operator is undoing). The op_id goes in as a comment in the PS output for traceability.

### Which site URL to connect to

PnP connects to a specific site URL; recycle bins are per-site. We need the drive's site URL from the `drive_id`:
```text
GET /drives/{drive_id} → webUrl
```
Then trim:
- Personal drive webUrl: `https://<tenant>-my.sharepoint.com/personal/<user_slug>/Documents` → trim trailing `/Documents` → `https://<tenant>-my.sharepoint.com/personal/<user_slug>`.
- Site drive webUrl: `https://<tenant>.sharepoint.com/sites/<site>/Shared%20Documents` → trim trailing `/<library-name>` → `https://<tenant>.sharepoint.com/sites/<site>`.

Safer: split on `/Shared%20Documents` or `/Documents`, take the left half, URL-decode. Handle the edge case where the library has a non-default name by using `GET /drives/{drive_id}/root` and finding the closest ancestor that's a `site` resource — but that's over-engineering; the two-prefix trim covers 99% of real drives.

### PS auth + script shape

Same prologue as `audit-sharing.ps1` / `Set-m365ctlLabel.ps1`:
```powershell
$pwd = ConvertTo-SecureString -String (
    /usr/bin/security find-generic-password -a m365ctl -s m365ctl:PfxPassword -w
) -AsPlainText -Force
Connect-PnPOnline -Tenant <tid> -ClientId <cid> `
    -CertificatePath $PfxPath -CertificatePassword $pwd -Url $SiteUrl
```

Output is one JSON line on stdout on success, `Write-Error` on failure (exits non-zero). Python parses stdout as JSON and uses the parsed record to populate the audit log `after` block.

---

## File structure (new + modified)

```
scripts/ps/
├── recycle-restore.ps1              # NEW
├── recycle-purge.ps1                # NEW
└── _m365ctlRecycleHelpers.ps1         # NEW: shared site-URL / lookup helpers (dot-sourced)

src/m365ctl/
├── mutate/
│   ├── delete.py                    # MODIFIED: execute_restore falls back to pwsh on notSupported
│   └── clean.py                     # MODIFIED: purge_recycle_bin_item falls back to pwsh on 404
└── cli/
    ├── undo.py                      # no change (already dispatches execute_restore)
    └── clean.py                     # no change

tests/
├── test_mutate_delete.py            # MODIFIED: +2 tests covering PS fallback path (subprocess mocked)
└── test_mutate_clean.py             # MODIFIED: +2 tests covering PS fallback path
```

No new Python files. No CLI surface change. `od-undo` and `od-clean recycle-bin` keep their current command shape; the new behaviour is entirely internal.

---

## Task 1: `scripts/ps/_m365ctlRecycleHelpers.ps1` — shared auth + lookup

**Files:**
- Create: `scripts/ps/_m365ctlRecycleHelpers.ps1`

Exports three functions, dot-sourced by the other two scripts:

- `Connect-m365ctlSite -Tenant -ClientId -PfxPath -SiteUrl` — wraps `Connect-PnPOnline` with cert+Keychain auth; throws if the Keychain entry is missing.
- `Find-RecycleBinItem -LeafName -DirName` — returns the single most-recent matching `RecycleBinItem` or throws with a specific error code (`NoMatch` / `AmbiguousMatch`). On ambiguity, log all matches to stderr, pick the newest by `DeletedDate`.
- `Resolve-SiteUrlFromDriveId -DriveId -TenantHost` — calls Microsoft Graph via `Invoke-PnPGraphMethod` (or a plain `Invoke-RestMethod` if PnP's helper isn't ideal) to fetch `/drives/{id}/webUrl`, trims, returns the site URL.

**Steps:**
- [ ] **Step 1:** Write the three functions as a single `.ps1` that can be dot-sourced.
- [ ] **Step 2:** Smoke-test via pwsh REPL: dot-source the file, call `Resolve-SiteUrlFromDriveId` with a known drive, verify it returns `https://example.sharepoint.com/sites/ServisOnboarding` (or whatever the live drive maps to).
- [ ] **Step 3:** Commit with message `feat(ps): shared PnP helpers for recycle-bin ops`.

No Python tests here — this file is exercised indirectly via the other two scripts. Add a `# shellcheck disable` equivalent comment if any linter complains about the dot-source-only pattern.

---

## Task 2: `scripts/ps/recycle-restore.ps1` + `execute_restore` fallback

**Files:**
- Create: `scripts/ps/recycle-restore.ps1`
- Modify: `src/m365ctl/mutate/delete.py`
- Modify: `tests/test_mutate_delete.py`

### Step 1: The PS script

Takes params:
```powershell
param(
    [Parameter(Mandatory=$true)][string]$Tenant,
    [Parameter(Mandatory=$true)][string]$ClientId,
    [Parameter(Mandatory=$true)][string]$SiteUrl,
    [Parameter(Mandatory=$true)][string]$LeafName,
    [Parameter(Mandatory=$true)][string]$DirName,
    [string]$PfxPath = "$HOME/.config/m365ctl/m365ctl.pfx"
)
```

Logic: dot-source helpers, connect, `Find-RecycleBinItem`, `Restore-PnPRecycleBinItem -Identity $rb.Id -Force`, then emit JSON on stdout:

```json
{
  "recycle_bin_item_id": "abc-123",
  "restored_name": "hello.txt",
  "restored_parent_path": "/Shared Documents/_m365ctl_smoke"
}
```

### Step 2: Python fallback in `execute_restore`

Modify `execute_restore` to keep the Graph call as the primary (no-op change for OneDrive-Personal). On `GraphError` containing one of `_ODFB_RESTORE_TOKENS`, call a new private helper `_restore_via_pnp(op, before) -> dict | None` that:
1. Looks up the site URL from `op.drive_id` (new helper: `_lookup_site_url(graph, drive_id) -> str`; reuses the webUrl-trim logic from the PS helpers but in Python for the token we already have).
2. Shells out to `pwsh -NoProfile -File scripts/ps/recycle-restore.ps1 -Tenant … -ClientId … -SiteUrl … -LeafName <name> -DirName <parent_path>`.
3. Parses stdout JSON.
4. Returns an `after` dict with the restored metadata.

If the PS call exits non-zero, propagate the PS stderr into `DeleteResult.error` (already the existing pattern) and keep the audit-end `result=error`. Keep the existing actionable-message wrapping for Graph errors that aren't in the ODfB token set (so weird tenant-specific issues still surface clearly).

### Step 3: Tests

Two new tests in `test_mutate_delete.py`, subprocess mocked:
- `test_restore_falls_back_to_pnp_on_notsupported` — Graph MockTransport returns 400 `notSupported`, `subprocess.run` is mocked to return 0 + the JSON payload; assert `result.status == "ok"` and `result.after["recycle_bin_item_id"] == "abc-123"`.
- `test_restore_pnp_failure_propagates_stderr` — Graph 400, `subprocess.run` returns non-zero with stderr `"Set-PnPRecycleBinItem: no match"`; assert `result.status == "error"` and `"no match"` is in the error.

Existing test `test_restore_notsupported_wraps_with_manual_instructions` needs to be adapted: with the new fallback, the "manual instructions" wrap should only fire when PS itself fails. Update the test to patch `subprocess.run` to raise `FileNotFoundError` (pwsh not installed), then assert the manual-instructions message is still emitted in that specific case.

### Step 4: Commit

`feat(restore): PnP.PowerShell fallback for ODfB recycle-bin restore`.

---

## Task 3: `scripts/ps/recycle-purge.ps1` + `purge_recycle_bin_item` fallback

**Files:**
- Create: `scripts/ps/recycle-purge.ps1`
- Modify: `src/m365ctl/mutate/clean.py`
- Modify: `tests/test_mutate_clean.py`

Symmetric to Task 2. Differences:
- PS script calls `Clear-PnPRecycleBinItem -Identity $rb.Id -Force` instead of `Restore-PnPRecycleBinItem`.
- On success emits `{"recycle_bin_item_id": "…", "purged_name": "…"}`.
- Python fallback triggers on Graph errors matching `_ODFB_PURGE_TOKENS` (itemNotFound/HTTP404/accessDenied).
- Tests mirror Task 2.

### Step 1: PS script

Same param shape as `recycle-restore.ps1` minus the restored-path return.

### Step 2: Python fallback in `purge_recycle_bin_item`

Mirror the `execute_restore` change. After fallback success, the `after` block should still have `"irreversible": True` — the op is permanent regardless of which code path ran. This preserves the invariant that `build_reverse_operation` raises `Irreversible` for any `od-clean(recycle-bin)` record.

### Step 3: Tests

Two new tests in `test_mutate_clean.py`:
- `test_purge_falls_back_to_pnp_on_404` — same shape as the restore fallback test.
- `test_purge_pnp_failure_propagates_stderr` — same.

Adapt `test_purge_404_wraps_with_manual_instructions` for the new behaviour, same way as in Task 2.

### Step 4: Commit

`feat(purge): PnP.PowerShell fallback for ODfB recycle-bin purge`.

---

## Task 4: Live smoke + docs + AGENTS.md note

**Files:**
- Modify: `docs/ops/pnp-powershell-setup.md` (small note; likely just a mention that the same `Sites.FullControl.All` permission covers these new ops).
- Modify: `AGENTS.md` (one-line note in the Mutation safety envelope section).
- Append to: `docs/superpowers/plans/2026-04-24-recycle-bin-odfb-followup.md` (completion log).

### Steps

- [ ] **Step 1:** Live smoke — stage `_m365ctl_smoke2/hello2.txt` via Graph direct (same pattern as Plan 4 Task 13 Step 2). Run:
    ```bash
    ./bin/od-delete --scope me --drive-id $D --item-id $I --confirm
    DELETE_OP=$(tail -1 logs/ops/$(date -u +%F).jsonl | python -c 'import sys,json; print(json.loads(sys.stdin.read())["op_id"])')
    ./bin/od-undo $DELETE_OP --confirm
    ```
    Expected: `[<uuid>] ok (reverse of $DELETE_OP)`. Verify the file reappears in `/_m365ctl_smoke2/` via `od-search --scope me "hello2.txt"`.
- [ ] **Step 2:** Re-delete, then purge via hand-crafted `--from-plan`:
    ```bash
    ./bin/od-delete --scope me --drive-id $D --item-id $I --confirm
    # build /tmp/purge2.json with action=recycle-purge
    ./bin/od-clean recycle-bin --from-plan /tmp/purge2.json --confirm
    PURGE_OP=$(tail -1 logs/ops/$(date -u +%F).jsonl | python -c '…')
    ./bin/od-undo $PURGE_OP --confirm
    ```
    Expected: purge prints `[<uuid>] ok`; undo exits 2 with `irreversible: op ... was a recycle-bin purge — items are permanently deleted ...`. This is the exact spec branch of `od-undo` that Plan 4 Task 13 couldn't verify because the purge itself errored.
- [ ] **Step 3:** Audit-log dump — 4 paired records, all `result: ok` except the final undo which never writes because `build_reverse_operation` raises before the first log call.
- [ ] **Step 4:** Full-suite run; expected delta: +4 tests from Tasks 2 and 3.
- [ ] **Step 5:** Update docs + AGENTS.md; commit; push.
- [ ] **Step 6:** Append completion log to this plan file.

---

## Spec invariants — nothing should regress

- Safety envelope (§7) is unchanged. `assert_scope_allowed` still runs before any shell-out. `--confirm` still required.
- Audit log contract: `start` record persists BEFORE the PS call (spec §7 rule 5). If pwsh crashes or is missing, an `end` record with `result=error` is still written.
- `od-undo` on a successful `od-clean(recycle-bin)` op STILL raises `Irreversible` — the `cmd == "od-clean(recycle-bin)"` branch in `build_reverse_operation` is orthogonal to whether the underlying purge used Graph or PS.
- OneDrive-Personal continues to work via the Graph path — the fallback only fires when Graph returns one of the specific ODfB error tokens.

## Estimated size

~250 lines of Python across modifications, ~150 lines of PowerShell, ~100 lines of test code. Four tasks, one live smoke. Full review only for Task 2 (the restore fallback) since the rest is symmetric or docs.

## Review tiering (matches Plan 3/4 conventions)

- **Full 3-stage review:** Task 2 (restore fallback) — data-recovery correctness. Getting the recycle-bin-id lookup wrong could restore the wrong file.
- **Implementer + quick diff:** Task 1 (helpers), Task 3 (purge — identical pattern to Task 2), Task 4 (docs + smoke).

---

## Completion log

**Completed (2026-04-24):** Tasks 1–4 landed on branch `plan-5/recycle-bin-odfb-followup`.

### Commits

- `52319c7` / `6ba6f55` — Task 1: `_m365ctlRecycleHelpers.ps1` (+ review fixups).
- `ecf4663` / `83308f9` — Task 2: restore fallback (+ data-recovery hazard fix: raise on unknown library suffix).
- `dc2ce07` — Cross-cutting refactor: shared `invoke_pwsh` and `lookup_site_url_from_drive_id` in `m365ctl/mutate/_pwsh.py`.
- `4ddc796` — Task 3: purge fallback.
- `7475988` — Task 4: docs + initial completion log.
- `a6409aa` — Close final-review gaps: add tests for lookup-fallback-through paths; warn on recycle-bin ceiling; fix latent `GraphError` constructor bug in `_pwsh.py`.
- `7207b29` — Live-smoke fix: thread delete `before` through the reverse op's args; normalize Graph-path `/drives/<id>/root:` prefix to site-relative for PnP's `-DirName`.
- `8dc0502` — Live-smoke fix: drop `-PfxPath cfg.cert_path` override (PEM key, not PFX); PS default at `~/.config/m365ctl/m365ctl.pfx` is correct.
- `d0a2949` — Live-smoke fix: recover purge `before` block from prior `od-delete` audit record so `Find-RecycleBinItem` has a real `LeafName`/`DirName` to match.

### Test delta

- Baseline (before this plan): 197 passed + 1 skipped.
- After: 216 passed + 1 skipped. (+19 tests across `test_mutate_delete.py`, `test_mutate_clean.py`, `test_mutate_undo.py`, `test_pwsh.py`, `test_audit.py`, `test_cli_clean.py`.)

Full-suite run at HEAD: `216 passed, 1 skipped`. The one skip is the live-gated `test_auth.py::test_live_whoami`, unchanged from Plan 4.

### Design deviations from the original plan

- Moved `_lookup_site_url` into `m365ctl/mutate/_pwsh.py` as `lookup_site_url_from_drive_id` (public in module) rather than keeping it in `delete.py`. Avoids a cross-mutate-module import when Task 3's `clean.py` needed the same helper. Same semantics; function now raises `GraphError("unknownLibrarySuffix", ...)` on unrecognized library suffix instead of the originally-implemented silent `rsplit` heuristic — the silent heuristic was a data-recovery hazard (wrong site URL → wrong recycle bin → could restore a different file with the same name).
- Extracted shared `invoke_pwsh` helper to avoid triplicate `subprocess.run(["pwsh", ...])` across `label.py`, `delete.py`, `clean.py`. Unplanned but code-review-driven.
- `Find-RecycleBinItem` ambiguity handling: the plan said "throws with a specific error code (`NoMatch` / `AmbiguousMatch`)" AND "On ambiguity, log all matches to stderr, pick the newest." Resolved to the second form — `AmbiguousMatch` is a `Write-Warning`; only `NoMatch` throws. Rationale: the most-recent delete is almost certainly the undo target, and failing-closed on ambiguity would block common restores.

### Live smoke — executed 2026-04-24

File: `_m365ctl_smoke2/hello2.txt` staged in the primary operator's personal OneDrive (`drive_id = b!3FSdMpv3t0Kf…pm_ga`, `item_id = 01KEZPQAHEAAZT7HM6BJG2DKB4VKTUZQCT`). Three commits (`7207b29`, `8dc0502`, `d0a2949`) were required to close bugs surfaced only by the live run — all now fixed and covered by new tests.

- **Step 1 — `od-delete` + `od-undo` restore round-trip:** ✅
  - Delete op: `8eaaaeaf-d060-4496-ae4b-b8a2325ddff9` → `[…] ok (recycled)`.
  - Undo op: `0b8b5af8-365e-4b6a-bf23-ec576524e802` → `[…] ok (reverse of 8eaaaeaf-…)`.
  - Post-restore verification via `GET /drives/{id}/root:/_m365ctl_smoke2/hello2.txt`: item present, same `item_id`.

- **Step 2 — re-delete + purge + undo-on-purge:** ✅
  - Re-delete op: `24ed444a-8632-4305-aefb-245acb7f87c6` → `[…] ok (recycled)`.
  - Purge op: `smoke-purge-24ed444a-8632-4305-aefb-245acb7f87c6` → `[…] ok`.
  - Undo-on-purge exit 2 with: `irreversible: op 'smoke-purge-…' was a recycle-bin purge — items are permanently deleted and not recoverable by this toolkit. If retention backup is available, contact Microsoft 365 admin.`

- **Step 3 — audit log:** four paired `start`/`end` records all `result: ok`, plus one failed purge `end` during bug investigation (expected). Undo-on-purge wrote nothing (raises `Irreversible` before the first `log_mutation_start` call). Representative records (same-day `logs/ops/2026-04-24.jsonl`):
  ```
  start od-delete                 op=8eaaaeaf...
  end                             op=8eaaaeaf... result=ok
  start od-undo(restore)          op=0b8b5af8...
  end                             op=0b8b5af8... result=ok
  start od-delete                 op=24ed444a...
  end                             op=24ed444a... result=ok
  start od-clean(recycle-bin)     op=smoke-purge-24ed444a...
  end                             op=smoke-purge-24ed444a... result=ok
  ```

### Bugs surfaced and fixed during live smoke

Worth calling out explicitly — these weren't visible from unit-tests alone:

1. **Reverse-op discarded the delete's `before` block** (`mutate/undo.py`). The restore reverse Operation had empty `args`, and at undo time `cli/undo.py` tried a live `_lookup_item` which 404s on recycle-bin items. Fix (`7207b29`): pack `orig_name`/`orig_parent_path` into the reverse op's `args`.

2. **Graph path format mismatches PnP `DirName`.** `before.parent_path` recorded at delete time is `/drives/<id>/root:/<path>`; PnP's recycle-bin `DirName` is site-relative (e.g. `personal/<user>/Documents/<path>`). The `-like "*$DirName"` match failed. Fix (`7207b29`): added `normalize_recycle_dir_name` in `_pwsh.py` that strips everything up to and including `root:`.

3. **`-PfxPath` was overridden with the PEM key** (`cfg.cert_path` is `.key`, not `.pfx`). PS tried to load `.key` as a certificate and failed. Fix (`8dc0502`): drop the override; the PS default at `~/.config/m365ctl/m365ctl.pfx` is the right path.

4. **Purge had no way to recover `before.name` when the item was already in the recycle bin.** `cli/clean.py` fell back to empty meta on the 404, sending `-LeafName ""` to PS. Fix (`d0a2949`): new audit helper `find_most_recent_delete_before` walks the log for a matching prior `od-delete` start record and uses its `before` block. Operator-facing warning surfaced when no such record exists.

### Intentionally deferred (per plan)

- Version-history restore (`od-clean old-versions` — unrecoverable by design).
- Stale-share re-issue (`od-clean stale-shares` — link URLs not reproducible).
- Batched recycle-bin ops (`Clear-PnPRecycleBinItem -All`).
- Cross-tenant restore.
