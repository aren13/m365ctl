# AGENTS.md — m365ctl

Notes for Claude Code and other agentic assistants working on this repo.
Terse on purpose — scannable > complete.

## Overview

m365ctl is a dual-domain CLI targeting Microsoft Graph:

- **OneDrive + SharePoint** — the Phase 0 focus; verbs exist today.
- **Mail** — scaffold only in Phase 0; Phase 1+ fills it out.

Common auth / config / safety plumbing lives in `common/`. Each domain is a
sibling sub-package.

## Where to start

1. `docs/superpowers/specs/2026-04-24-m365ctl-design.md` — the parent design spec (OneDrive track, architecture, safety model).
2. `docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md` — the Mail module spec (Phase 1 target).
3. `CONTRIBUTING.md` — dev setup, test commands, commit conventions.
4. `docs/setup/first-run.md` — tenant setup prerequisites.

## Package layout

- `src/m365ctl/common/` — auth, graph, config, audit, safety, retry, planfile,
  `undo.Dispatcher`.
- `src/m365ctl/onedrive/` — catalog, download, mutate, search, cli (OneDrive
  domain).
- `src/m365ctl/mail/` — `catalog/`, `mutate/`, `triage/`, `cli/` scaffolds.
  Empty in Phase 0.
- `src/m365ctl/cli/` — top-level dispatcher (`m365ctl <domain> <verb>`).

## Safety envelope

- Dry-run is the default. Every mutation requires `--confirm`.
- Bulk operations use the plan-file workflow: generate → review → replay.
- Scope gates:
  - Allow-lists: `allow_drives`, `allow_mailboxes` (empty = allow-all).
  - Deny-lists: `deny_paths`, `deny_folders` (absolute; always enforced).
- Audit log: `logs/ops/YYYY-MM-DD.jsonl`. Every mutation records
  `before` / `after` blocks.
- Undo: `m365ctl.common.undo.Dispatcher` replays inverses from the audit log.
  Irreversible ops are flagged at registration time and refuse to undo.

## Key conventions

- TDD — write the test first. See `tests/` for the pattern.
- Audit capture is load-bearing: never drop the `before` block even when the
  state is partially known (e.g. item already in recycle bin).
- Plan-file actions are namespaced: `od.move`, `od.rename`, etc. Bare-action
  legacy plans are normalized on read for back-compat.

## Running tests

```bash
# Unit + mocked integration (default):
uv run pytest -m "not live"

# Live smoke (needs tenant + config.toml):
M365CTL_LIVE_TESTS=1 uv run pytest -m live
```

## PowerShell prerequisites

`od-audit-sharing` and a handful of label/restore/purge paths shell out to
PnP.PowerShell. See `docs/ops/pnp-powershell-setup.md`. The PFX password is
kept in macOS Keychain under service `m365ctl`, account `PfxPassword`.

## Tenant-agnostic rule

No concrete tenant values in tracked code, tests, or docs:

- UPNs and domains → `user@example.com`, `contoso.com`.
- Tenant / client IDs → placeholders in `config.toml.example`.
- Site URLs → `https://contoso.sharepoint.com/...`.

The only allowed exception is `docs/setup/migrating-from-fazla-od.md`, which
documents the old `fazla-od` names for the one-time migration.
