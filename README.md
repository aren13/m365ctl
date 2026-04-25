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
- **Inbox rules CRUD (Phase 8):** `mail rules {create|update|delete|
  enable|disable|reorder|export|import}` — round-trippable YAML
  pipeline. `mail rules export --out a.yaml` then
  `mail rules import --from-file a.yaml --replace --confirm` rebuilds
  the rule set. Audit + undo intact.
- **Mailbox settings (Phase 9):** `mail settings {timezone, working-hours}`,
  `mail ooo {show, on, off}` with scheduled-OOO + 60-day safety gate, and
  `mail signature {show, set}` over a local-file fallback. All mutations
  audit-logged and undoable.
- **Export (Phase 11):** `mail export {message,folder,mailbox,attachments}`
  — per-message EML, streaming MBOX, attachment dump, and full-mailbox
  manifest with resume-on-interrupt. All read-only.
- **Convenience verbs (Phase 14, 1.0):** `mail {digest, archive, snooze,
  unsubscribe, top-senders, size-report}` — daily-driver composition over
  the core surface. See `docs/mail/convenience-commands.md` for each one's
  synopsis and example output.
- **Hard delete (Phase 6, 1.1):** `mail clean <id>`, `mail clean recycle-bin`,
  `mail empty <folder>` — irreversible deletes with full EML capture to
  `[logging].purged_dir` BEFORE the wire-delete. Triple-gated: `--confirm`,
  TTY-typed phrase, and a common-folder/≥1000-item escalation.
- **Multi-mailbox + delegation (Phase 12, 1.2):** every shipped verb
  accepts `--mailbox shared:<addr>` for shared-mailbox routing.
  `mail delegate {list,grant,revoke} --rights …` manages FullAccess /
  SendAs / SendOnBehalf via Exchange Online PowerShell with audit + undo.
- **Scheduled send (Phase 5b, 1.3):** `mail send <draft> --schedule-at <iso>`
  defers delivery via the MAPI `PR_DEFERRED_DELIVERY_TIME` extended
  property. Gated behind `[mail].schedule_send_enabled`.
- **Send-as (Phase 13, 1.4):** `mail sendas <from-upn> --to <addr> ... --confirm`
  sends as another mailbox via app-only `/users/{upn}/sendMail`. Both the
  effective sender and the authenticated principal are audit-logged.
- **Chunked attachments (Phase 5a-2, 1.5):** `mail attach add <msg>
  --file <≥3MB-file> --confirm` streams via Graph's upload-session
  protocol. 4 MB chunks, no in-memory buffering.
- **DSL predicates extended (Phase 10.x, 1.6):** triage rules now
  support `to`, `body`, `cc`. Catalog schema bumped to v2 (additive
  `cc_addresses` migration; existing catalogs auto-upgrade on next
  refresh).
- **Thread predicate (Phase 10.y, 1.7):** `thread: { has_reply: false }`
  catches sent mail with no reply yet. Pure catalog reasoning, no per-
  message Graph fetches.
- **Mid-folder export resume (Phase 11.x, 1.8):** `mail export mailbox`
  now resumes interrupted folders message-by-message via
  `last_exported_id` checkpoints in the manifest. Killing mid-export
  and re-running picks up where it left off; no re-uploads.
- **Catalog refresh perf (Phase 7.x, 1.9):** `/messages/delta` now uses
  `$select` for the ~19 fields the catalog reads (~80% payload trim),
  and DuckDB upserts batch into one transaction per round. Targets
  first-time large-mailbox onboarding.
- **Headers predicate (Phase 10.z, 1.10):** `headers: { name: List-Unsubscribe, contains: example.com }`
  matches against `internetMessageHeaders` with lazy per-message fetch
  + per-run cache. Rulesets without headers predicates pay zero overhead.
- **Soft-delete-undo cleanup (Phase 4.x, 1.11):** `m365ctl undo` for
  `mail.delete.soft` now handles manually-moved-out-of-Deleted-Items
  and already-restored cases without falling back to "restore manually".

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
