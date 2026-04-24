# Resumption notes — m365ctl

## Phase 0 complete — 2026-04-24

- Package renamed to `m365ctl` (from the prior internal name).
- Restructured into `common/` + `onedrive/` + `mail/` sub-packages.
- Mail tree scaffold only (empty). Phase 1 adds readers.
- Undo dispatcher extracted to `m365ctl.common.undo.Dispatcher`.
- Config extended with `[mail]`, `allow_mailboxes`, `deny_folders`, `purged_dir`, `retention_days`.
- Apache-2.0 LICENSE, README, CONTRIBUTING, CHANGELOG, CI, setup docs shipped.
- All §4.5 grep assertions clean (see Group 11 acceptance-gate pass).
- Known baseline: ~31 pre-existing mypy errors in OneDrive CLI/undo code; CI uses `|| true`. Clean up as a follow-up group.

### Phase 0.5 deferred items (track for early Phase 1)

These surfaced during Group 11 acceptance gates and are documented
deferrals — not regressions:

1. **PowerShell operational defaults still use `fazla-od` names.** PFX
   filename (`~/.config/fazla-od/fazla-od.pfx`), keychain account
   (`"fazla-od"`), and `convert-cert.sh` defaults were not renamed in
   Phase 0 because flipping them forces an on-disk + Keychain migration
   for existing users. When Phase 1 opens, rename to `m365ctl.{key,cer,pfx}`
   + account `m365ctl` and extend `docs/setup/migrating-from-fazla-od.md`
   with the new migration steps. Affected files: `scripts/ps/convert-cert.sh`,
   `scripts/ps/_M365ctlRecycleHelpers.ps1`, `scripts/ps/audit-sharing.ps1`,
   `scripts/ps/recycle-{purge,restore}.ps1`, `src/m365ctl/onedrive/mutate/{delete,clean}.py`
   comments, `docs/ops/pnp-powershell-setup.md` body.
2. **`scripts/ps/audit-sharing.ps1:111`** — the `@fazla\.` external-user
   regex is tenant-identifying functional code. Externalize to a
   `ScopeConfig.internal_domain_pattern` when Phase 1 touches ScopeConfig
   for `allow_mailboxes` enforcement.

Next session: Phase 1 — mail readers (list, get, search, folders, categories, rules, settings, attachments). See `docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md` §19 Phase 1. Author a plan via `superpowers:writing-plans` before executing.

*Refreshed 2026-04-24 after Plan 5's loose-ends cleanup pass.*

## State

Plans 1-5 are complete. `main` is at `d786af7`.

- Plan 1 — auth (device-code + app-only cert), committed.
- Plan 2 — catalog crawler (delta, DuckDB), committed.
- Plan 3 — search + download + read-only inventory CLIs, committed.
- Plan 4 — mutations (move/rename/copy/delete/label/clean) + safety + undo, committed.
- Plan 5 — recycle-bin restore/purge via PnP.PowerShell, label via PnP.PowerShell, live smoke + bugfixes; final review follow-ups (this branch), ends at `d786af7`.

Tests: 217 passed + 1 skipped at HEAD (216 before this cleanup branch's `test_label_apply_handles_pwsh_missing`). The one skip is `tests/test_auth.py::test_live_whoami`, a live-tenant smoke guarded by `M365CTL_LIVE_TESTS=1`.

`AGENTS.md` is the authoritative operator reference — it's kept current alongside the code.

## CLI surface

See `AGENTS.md` for the full table. One-line version: `od-auth`, `od-catalog-refresh`, `od-catalog-status`, `od-inventory`, `od-search`, `od-download`, `od-audit-sharing`, `od-move`, `od-rename`, `od-copy`, `od-delete`, `od-clean`, `od-label`, `od-undo`. All mutating commands dry-run by default and require `--confirm`; bulk patterns require the plan-file workflow.

## Deferred / out of scope

These were deliberately punted and are safe to defer further:

- **MCP server front-end** (once contemplated as Plan 6) — the CLI is the supported interface today.
- **Version-history restore** — `od-clean old-versions` is irreversible; there is no paired restore command.
- **Stale-share re-issue** — `od-clean stale-shares` revokes but does not re-create equivalent links.
- **Batched recycle-bin ops** — the PS helpers drive one item per invocation; bulk recycle-bin workflows would page differently.
- **Cross-tenant restore** — `od-undo` assumes same tenant; no import from external audit logs.
- **True paging for `Find-RecycleBinItem`** — currently capped at 100000 per call with a warning when hit. PnP.PowerShell has no native page cursor here; future enhancement would split by FirstStage / SecondStage.

## Gotchas worth remembering

1. **`retry.py` asymmetric contract.** `max_attempts <= 1` re-raises the underlying exception with its type/attrs intact; `max_attempts >= 2` wraps exhaustion in `RetryExhausted`. Don't collapse the branches.

2. **`_enumerate_tenant` has a `_collect` fallback.** When `graph.get_paginated` returns an empty iterator, `_collect` falls back to `graph.get(path).value`. The `except` is narrowed to `(TypeError, AttributeError)` so real Graph errors still propagate.

3. **`prompts.confirm_or_abort` wraps `OSError` at the call site** (not only inside `_open_tty`) so monkeypatching `_open_tty` to raise `OSError` still surfaces as `TTYUnavailable`.

4. **`/dev/tty` confirms cannot be bypassed by agents** — that's the point. Use `--yes` on scripted runs or drive the terminal interactively.

5. **PnP.PowerShell fallbacks need a PFX, not the PEM key.** The PS scripts default `-PfxPath` to `~/.config/m365ctl/m365ctl.pfx` populated via `scripts/ps/convert-cert.sh`; Python does not pass its own `cfg.cert_path` through (that's the PEM). See `docs/ops/pnp-powershell-setup.md`.

## Resuming work

Open a fresh session in this repo. If you're picking up where things left off:

- Run `uv run pytest` to confirm 217 passed + 1 skipped.
- `./bin/od-auth whoami` to confirm both flows still work and the cert isn't expiring soon (expiry 2028-04-22).
- If the catalog is stale, `./bin/od-catalog-refresh --scope me` before running inventory or search commands against fresh data.
- For a new feature, read the spec at `docs/superpowers/specs/2026-04-24-m365ctl-design.md` and the deferred list above before writing a plan.
