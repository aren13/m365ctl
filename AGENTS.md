# AGENTS.md - Fazla OneDrive Toolkit

Notes for Claude Code (and any agentic assistant) operating this repo.

## What this is

A CLI for admin-scoped control of the Fazla M365 tenant's OneDrive + SharePoint content via Microsoft Graph. The full design is in `docs/superpowers/specs/2026-04-24-fazla-onedrive-toolkit-design.md`. Plans are under `docs/superpowers/plans/`.

## Current CLI surface (Plans 1-2 complete)

| Command | Purpose |
|---|---|
| `./bin/od-auth login` | Device-code delegated sign-in; caches token. |
| `./bin/od-auth whoami` | Identity (delegated + app-only), cert expiry, tenant. |
| `./bin/od-catalog-refresh --scope me\|drive:<id>` | Delta-crawl a scope into `cache/catalog.duckdb`. |
| `./bin/od-catalog-status` | Print catalog summary: drives, items, bytes. |
| `./bin/od-inventory --top-by-size N` | Top N largest live files. |
| `./bin/od-inventory --stale-since YYYY-MM-DD` | Files not modified since date. |
| `./bin/od-inventory --by-owner` | File count + total size per owner. |
| `./bin/od-inventory --duplicates` | Items sharing a `quickXorHash`. |
| `./bin/od-inventory --sql "<SELECT ...>"` | Ad-hoc SELECT against the catalog. |

All inventory commands accept `--json` for machine-readable output.

All other commands from the spec (`od-search`, `od-move`, `od-label`, ...) are delivered in later plans.

## Safety model (already in effect)

- `config.toml` is **gitignored**. Never `git add` it. The tracked template is `config.toml.example`.
- Cert private key is at `~/.config/fazla-od/fazla-od.key` (mode 600) - outside this repo. Never read, cat, or commit it.
- `cache/`, `workspaces/`, `logs/` are gitignored runtime dirs.

When mutating commands ship (Plan 4):
- `--dry-run` is always the default; `--confirm` is required to execute.
- Bulk ops require the plan-file workflow (`--plan-out` -> review -> `--from-plan`).
- See spec §7 for the full model. Follow it.

## Authentication at a glance

- **Delegated** (`./bin/od-auth login`): device-code; user signs in once, token cached in `~/.config/fazla-od/token_cache.bin`.
- **App-only**: certificate-based, zero user interaction per run. Used automatically by commands that need tenant-wide access.

Both flows run against the same Entra app; admin consent is granted for both.

## Running tests

```bash
uv sync --extra dev
uv run pytest          # unit + mocked
FAZLA_OD_LIVE_TESTS=1 uv run pytest -m live    # hits real Graph
```
