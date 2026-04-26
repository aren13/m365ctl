# Resumption notes — m365ctl

*Refreshed 2026-04-26 against HEAD `e84d756` (release 1.11.1). Phase 0.5 deferrals closed in [Unreleased] — see `CHANGELOG.md`.*

## State

`main` at `e84d756` — release **1.11.1**. Both domain tracks are shipped end-to-end:

- **OneDrive / SharePoint** — auth, catalog, search, download, mutations (move/rename/copy/delete/label/clean), audit-sharing, undo, recycle-bin via PnP.PowerShell.
- **Mail** — readers, folder + category CRUD, soft + hard delete, compose / scheduled send / send-as, mailbox settings (OOO, signature, timezone, working hours), server-side rules CRUD, multi-mailbox + delegation, export (EML/MBOX/attachments) with mid-folder resume, triage DSL (folder / age / from / subject / body / to / cc / thread / headers predicates), convenience wrappers (digest, top-senders, size-report, focus, snooze, archive, unsubscribe, flag, etc.), and undo with id-rotation + manual-move recovery.

`bin/` exposes 52 verbs — 13 `od-*`, 38 `mail-*`, plus the unified `m365ctl-undo` dispatcher.

Tests: **957 passed + 1 skipped** on the [Unreleased] working tree (953 + 1 at the 1.11.1 tag — the four new tests come from the Phase 0.5 closeout). The skip is `tests/test_auth.py::test_live_whoami`, the live-tenant smoke guarded by `M365CTL_LIVE_TESTS=1`.

`AGENTS.md` is the authoritative operator reference and is kept current alongside the code.

## CLI surface

`m365ctl <domain> <verb>` is the entry point; the `bin/` shims are convenience aliases. All mutating commands dry-run by default and require `--confirm`; bulk patterns require the plan-file workflow (actions namespaced `od.move`, `mail.delete`, etc.).

## Phase 0.5 deferrals — closed on the [Unreleased] tree

Both items from the original Phase 0 hand-off are now resolved on `main`'s working tree (cut a release to ship them):

1. **PowerShell defaults renamed `fazla-od` → `m365ctl`.** New defaults: `~/.config/m365ctl/m365ctl.pfx` + Keychain account `m365ctl`. Legacy `fazla-od` paths/account continue to work as silent fallbacks across `audit-sharing.ps1`, `recycle-{purge,restore}.ps1`, and the shared `_M365ctlRecycleHelpers.ps1` (one-line stderr deprecation notice when the legacy entry is used). `convert-cert.sh` writes to the new defaults. Migration guide §5 documents the cleanup steps. *Untouched (correctly):* `src/m365ctl/common/auth.py` `_LEGACY_CACHE_DIR` / `_LEGACY_CACHE_FILE` — these are the MSAL token-cache migration path and must keep referencing `fazla-od`.

2. **Tenant-identifying `@fazla\.` regex removed.** `scripts/ps/audit-sharing.ps1` now accepts `-InternalDomainPattern <regex>`; `od-audit-sharing` reads `[scope].internal_domain_pattern` from `config.toml` and passes it through when set. Default behaviour is strictly more conservative — every `@`-bearing principal is treated external — so installs that were silently relying on the old hard-coded regex must add their own pattern to `config.toml` to restore the previous classification.

## Deferred / out of scope

Punted at design time and safe to defer further:

- **MCP server front-end** — once contemplated as a follow-on; the CLI is the supported interface today.
- **Version-history restore** — `od-clean old-versions` is irreversible; no paired restore command.
- **Stale-share re-issue** — `od-clean stale-shares` revokes but does not re-create equivalent links.
- **Batched recycle-bin ops** — PS helpers drive one item per invocation; bulk recycle-bin workflows would page differently.
- **Cross-tenant restore** — `m365ctl-undo` assumes same tenant; no import from external audit logs.
- **True paging for `Find-RecycleBinItem`** — capped at 100000 per call with a warning when hit. PnP.PowerShell has no native page cursor here; future enhancement would split by FirstStage / SecondStage.

## Gotchas worth remembering

These are codebase invariants, not phase-bound:

1. **`retry.py` asymmetric contract.** `max_attempts <= 1` re-raises the underlying exception with its type/attrs intact; `max_attempts >= 2` wraps exhaustion in `RetryExhausted`. Don't collapse the branches.

2. **`_enumerate_tenant` has a `_collect` fallback.** When `graph.get_paginated` returns an empty iterator, `_collect` falls back to `graph.get(path).value`. The `except` is narrowed to `(TypeError, AttributeError)` so real Graph errors still propagate.

3. **`prompts.confirm_or_abort` wraps `OSError` at the call site** (not only inside `_open_tty`) so monkeypatching `_open_tty` to raise `OSError` still surfaces as `TTYUnavailable`.

4. **`/dev/tty` confirms cannot be bypassed by agents** — that's the point. Use `--yes` on scripted runs or drive the terminal interactively.

5. **PnP.PowerShell fallbacks need a PFX, not the PEM key.** PS scripts default `-PfxPath` to `~/.config/m365ctl/m365ctl.pfx` (with a silent fallback to the legacy `~/.config/fazla-od/fazla-od.pfx`), populated via `scripts/ps/convert-cert.sh`; Python does not pass its own `cfg.cert_path` through (that's the PEM). See `docs/ops/pnp-powershell-setup.md`.

6. **Mail `undo` recovers from id rotation and manual moves.** `mail.delete.soft` undo uses `find_message_anywhere` (search by `internetMessageId`) when the original message id has rotated or when the user dragged the message out of Deleted Items between soft-delete and undo. If the user already dragged it back to the source folder, undo short-circuits with an informational notice and exits 0.

7. **Triage `headers` predicate fetches lazily.** A Graph GET with `?$select=internetMessageHeaders` is issued only when a `headers` predicate gates a row's decision; multiple `headers` predicates on the same row share one fetch. Rulesets without `headers` predicates incur zero per-message overhead.

8. **Catalog refresh `$select` is load-bearing for perf.** `_drain_delta` passes `$select` on the first `/messages/delta` call listing only the ~19 fields `normalize_message` reads. Removing it ~5×s the wire payload. DuckDB upserts in each round are wrapped in a single `BEGIN`/`COMMIT`.

9. **Plan-file actions are namespaced** (`od.move`, `mail.delete`, …). Bare-action legacy plans are normalized on read for back-compat — don't drop the normalization without a deprecation window.

## Resuming work

Open a fresh session in this repo. If picking up where things left off:

- `uv pip install -e .` if `uv run pytest` fails with `ModuleNotFoundError: m365ctl` (the editable install can drift if the venv is shared across worktrees).
- `uv run pytest` should report **957 passed + 1 skipped** on the [Unreleased] tree (953 + 1 at the 1.11.1 tag).
- `./bin/od-auth whoami` (or `./bin/mail-auth whoami`) to confirm both flows still work and the cert isn't expiring soon (expiry 2028-04-22).
- If the catalog is stale, `./bin/od-catalog-refresh --scope me` or `./bin/mail-catalog-refresh` before running inventory / search / triage commands against fresh data.

For new work:

- Read the relevant spec under `docs/superpowers/specs/` and skim the closest prior plan in `docs/superpowers/plans/` for the established structure (28 plan files exist; phases 0 → 14 + x/y/z follow-ups).
- Author a plan via `superpowers:writing-plans` before executing anything non-trivial.
