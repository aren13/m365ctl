# Resumption notes — m365ctl

*Refreshed 2026-04-27 against HEAD `77c5218` (release 1.12.0). Phase 0.5 deferrals shipped in 1.12.0 — see `CHANGELOG.md`.*

## State

`main` at `77c5218` — release **1.12.0**. Both domain tracks are shipped end-to-end:

- **OneDrive / SharePoint** — auth, catalog, search, download, mutations (move/rename/copy/delete/label/clean), audit-sharing, undo, recycle-bin via PnP.PowerShell.
- **Mail** — readers, folder + category CRUD, soft + hard delete, compose / scheduled send / send-as, mailbox settings (OOO, signature, timezone, working hours), server-side rules CRUD, multi-mailbox + delegation, export (EML/MBOX/attachments) with mid-folder resume, triage DSL (folder / age / from / subject / body / to / cc / thread / headers predicates), convenience wrappers (digest, top-senders, size-report, focus, snooze, archive, unsubscribe, flag, etc.), and undo with id-rotation + manual-move recovery.

`bin/` exposes 52 verbs — 13 `od-*`, 38 `mail-*`, plus the unified `m365ctl-undo` dispatcher.

Tests: **958 passed + 1 skipped** at the v1.12.0 tag (953 at v1.11.1; the +5 are the four Phase 0.5 closeout tests plus one whoami catalog-status test). The skip is `tests/test_auth.py::test_live_whoami`, the live-tenant smoke guarded by `M365CTL_LIVE_TESTS=1`.

`AGENTS.md` is the authoritative operator reference and is kept current alongside the code.

## CLI surface

`m365ctl <domain> <verb>` is the entry point; the `bin/` shims are convenience aliases. All mutating commands dry-run by default and require `--confirm`; bulk patterns require the plan-file workflow (actions namespaced `od.move`, `mail.delete`, etc.).

## Notable changes since v1.11.1 (all in v1.12.0)

- **PowerShell defaults renamed `fazla-od` → `m365ctl`.** New defaults: `~/.config/m365ctl/m365ctl.pfx` + Keychain account `m365ctl`. Legacy `fazla-od` paths/account continue to work as silent fallbacks across all PnP scripts (`audit-sharing.ps1`, `recycle-{purge,restore}.ps1`, `Set-M365ctlLabel.ps1`, and the shared `_M365ctlRecycleHelpers.ps1`) with a one-line stderr deprecation notice. `convert-cert.sh` writes to the new defaults. Migration guide §5 documents the cleanup. *Untouched (correctly):* `src/m365ctl/common/auth.py` `_LEGACY_CACHE_DIR` / `_LEGACY_CACHE_FILE` are the MSAL token-cache migration path and must keep referencing `fazla-od`.
- **`od-audit-sharing` default got more conservative — operators upgrading from 1.11.x must verify config.** The previously hard-coded `@fazla\.` regex is gone. `scripts/ps/audit-sharing.ps1` now accepts `-InternalDomainPattern`; `od-audit-sharing` reads `[scope].internal_domain_pattern` from `config.toml`. With it unset (the default), every `@`-bearing principal is treated external. Anyone who silently relied on the old classification must add their tenant's pattern to `config.toml`.
- **`od-label` apply/remove now actually works.** `Set-M365ctlLabel.ps1` was wired to undocumented env vars no caller ever set, so the live path failed at `Connect-PnPOnline`. Converted to the parameter-driven pattern; `execute_label_apply`/`execute_label_remove` now require keyword-only `cfg: Config`. Both in-tree call sites updated; external Python callers must add `cfg=`.
- **`od-auth whoami` reports real catalog status** (path + size, or a build hint) instead of the stale `"not yet built (Plan 2)"` placeholder that fired regardless of state.

## Deferred / out of scope

See `docs/roadmap.md` for the full forward-looking list (sibling modules,
MCP front-end, mail features, OneDrive feature gaps, open questions).
That doc is the single source of truth — don't duplicate items here.

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
- `uv run pytest` should report **958 passed + 1 skipped** at the v1.12.0 tag.
- `./bin/od-auth whoami` (or `./bin/mail-auth whoami`) to confirm both flows still work and the cert isn't expiring soon (expiry 2028-04-22).
- If the catalog is stale, `./bin/od-catalog-refresh --scope me` or `./bin/mail-catalog-refresh` before running inventory / search / triage commands against fresh data.

For new work:

- Read `AGENTS.md` for architecture + conventions, `docs/roadmap.md` for forward-looking direction (sibling modules, MCP front-end, mail features), and the latest few `CHANGELOG.md` entries for recent decisions.
- Author a plan via `superpowers:writing-plans` before executing anything non-trivial. Prior phase plans (the 28 files at `docs/superpowers/plans/`) were deleted in the public-release prep — see git history pre-`v1.12.1` for examples of the structure.
