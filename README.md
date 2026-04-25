# m365ctl

**m365ctl** is an admin CLI for Microsoft 365 OneDrive + SharePoint + Mail,
built on Microsoft Graph. Safe by default: dry-run, plan-file workflow, scope
allow-lists, audit log, and undo on every mutation.

![CI](https://github.com/aren13/m365ctl/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-Apache--2.0-green)

## Features

**OneDrive + SharePoint**

- Catalog mirror in DuckDB (fast local queries).
- Server + local search.
- Inventory (top-by-size, top-by-age, duplicates).
- Move / rename / copy with pre-flight checks.
- Sensitivity labels (read, set, audit).
- Sharing audit (permissions snapshot + drift reports).
- Recycle-bin purge with restore support.
- Download (bulk + resumable).

**Mail (Phase 1+, scaffolded in Phase 0)**

- List, get, search messages.
- Folders + categories management.
- Inbox rules CRUD.
- Move / copy / delete / flag / categorize.
- Focused-Inbox override.
- Compose, reply, forward, scheduled send.
- Out-of-office + signature management.
- Triage DSL for bulk rules.
- Export to EML / MBOX.
- **Catalog (Phase 7):** `mail catalog refresh` mirrors folders + messages
  into `cache/mail.duckdb` via Graph `/delta`; `mail catalog status` and
  `mail search --local` query the cache offline.
- **Triage DSL (Phase 10):** `mail triage validate <yaml>` and
  `mail triage run --rules <yaml> [--plan-out|--confirm]` — YAML rules
  match against the local catalog and emit a tagged plan that reuses
  the existing audit/undo paths. Examples in `scripts/mail/rules/`.

## Quickstart

```bash
git clone https://github.com/<you>/m365ctl
cd m365ctl
uv sync --all-extras
cp config.toml.example config.toml
# Follow docs/setup/first-run.md to fill in config + cert.
./bin/od-auth login
./bin/od-auth whoami
```

`uv` not installed yet? See https://docs.astral.sh/uv/.

## Setup

- [docs/setup/azure-app-registration.md](docs/setup/azure-app-registration.md) — create the Entra app and grant permissions.
- [docs/setup/certificate-auth.md](docs/setup/certificate-auth.md) — generate + upload the client cert for app-only flows.
- [docs/setup/first-run.md](docs/setup/first-run.md) — end-to-end, ≤ 20 minutes from `git clone` to `od-auth whoami`.

## Safety model

- **Dry-run default.** Every mutating verb requires `--confirm` to act.
- **Plan-file workflow.** Bulk ops are reviewed as a plan file before replay.
- **Scope allow-lists.** `allow_drives` / `allow_mailboxes` gate every call; `deny_paths` / `deny_folders` are absolute.
- **Undo via audit log.** Each mutation records a before/after block; `m365ctl undo <op-id>` replays the inverse.

## Commands

Per-command docs land in `docs/` as verbs stabilize. For the sharing-audit
PowerShell prerequisites see
[docs/ops/pnp-powershell-setup.md](docs/ops/pnp-powershell-setup.md).

Top-level entry points live in `bin/` (e.g. `od-auth`, `od-search`,
`od-inventory`, `od-move`, `od-undo`). Each accepts `--help`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0. See [LICENSE](LICENSE).

## Disclaimer

This is an independent open-source project. Not affiliated with Microsoft.
