# Changelog

All notable changes to m365ctl are documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.2.0] — 2026-04-24

### Added
- **Mail domain reader surface.**
  - `m365ctl mail list` — OData-filtered message list (`--folder`, `--unread`, `--read`, `--from`, `--subject`, `--since`, `--until`, `--has-attachments`, `--importance`, `--focus`, `--category`, `--limit`, `--json`).
  - `m365ctl mail get` — fetch one message, optionally with body and attachments. `--eml` flag deferred to Phase 11.
  - `m365ctl mail search` — server-side Graph `/search/query`. `--local` flag deferred to Phase 7.
  - `m365ctl mail folders` — tree/flat folder list with counts; hardcoded deny list filters out compliance buckets (`Recoverable Items`, `Purges`, `Audits`, `Calendar`, `Contacts`, `Tasks`, `Notes`).
  - `m365ctl mail categories` — master category list (CRUD lands Phase 2).
  - `m365ctl mail rules` — inbox rule list/show (CRUD lands Phase 8).
  - `m365ctl mail settings` — mailbox settings + OOO view (set lands Phase 9).
  - `m365ctl mail attach` — list + get attachments (add/remove lands Phase 5a).
  - `m365ctl mail whoami` — identity, declared scopes, delegated probe on `/me/mailFolders/inbox`, cert expiry, catalog stub. Surfaces admin-consent URL on 403.
- `m365ctl.mail.models` — 10 frozen dataclasses with `from_graph_json` parsers: `Message`, `Folder`, `Category`, `Rule`, `Attachment`, `MailboxSettings`, `EmailAddress`, `Body`, `Flag`, `AutomaticRepliesSetting`, `LocaleInfo`, `WorkingHours`.
- `m365ctl.mail.endpoints.user_base(spec, *, auth_mode)` + `parse_mailbox_spec` — `/me` vs `/users/{upn}` routing per mailbox spec.
- `m365ctl.common.safety.assert_mailbox_allowed` + `is_folder_denied` + `HARDCODED_DENY_FOLDERS` frozenset.
- `GraphClient.get_bytes(path)` — raw byte fetch for attachment content.
- `bin/mail-auth`, `bin/mail-whoami`, `bin/mail-list`, `bin/mail-get`, `bin/mail-search`, `bin/mail-folders`, `bin/mail-categories`, `bin/mail-rules`, `bin/mail-settings`, `bin/mail-attach` — short wrappers.
- `m365ctl mail` top-level route dispatched to the mail sub-package (replacing the Phase 0 "not yet implemented" stub).

### Changed
- `GRAPH_SCOPES_DELEGATED` extended with `Mail.ReadWrite`, `Mail.Send`, `MailboxSettings.ReadWrite`. **Requires admin re-consent** on the Entra app.
- `Message.from_graph_json` now raises `ValueError` (not `assert`) on missing `receivedDateTime` — safe under `python -O`.

### Migration
- Grant admin consent for the three new delegated scopes. Existing users running 0.1.0 must re-run `./bin/od-auth login` (or `./bin/mail-auth login`, they share a cache) after consent to pick up the expanded scope set. Until re-consent, delegated mail calls return HTTP 403 with `AccessDenied`; `mail-whoami` surfaces the Entra consent URL automatically.

## [0.1.0] — 2026-04-24

### Changed
- **Breaking:** Renamed package from `fazla_od` to `m365ctl`.
- **Breaking:** Package restructured into `common/`, `onedrive/`, `mail/` sibling sub-packages. See `docs/setup/migrating-from-fazla-od.md`.
- **Breaking:** Config directory moved from `~/.config/fazla-od/` to `~/.config/m365ctl/` (auto-migrated on first run).
- **Breaking:** Keychain items renamed (`FazlaODToolkit:*` → `m365ctl:*`). User must delete legacy items manually (see migration doc).
- **Breaking:** Environment variable `FAZLA_OD_LIVE_TESTS` renamed to `M365CTL_LIVE_TESTS`. Legacy name accepted with a deprecation warning for one minor version.
- **Breaking:** Plan-file actions now namespaced (`od.move` not `move`). Pre-refactor plans continue to parse via legacy-action normalization.

### Added
- Apache-2.0 LICENSE.
- README quickstart (tenant-agnostic).
- CONTRIBUTING.md.
- GitHub Actions CI: ruff + mypy + pytest (unit + mocked integration) on Python 3.11/3.12/3.13 × Ubuntu/macOS.
- `m365ctl.common.undo.Dispatcher` — domain-agnostic undo registry.
- `m365ctl undo` cross-domain entry point (currently alias for `m365ctl od undo`).
- Config fields `[scope].allow_mailboxes`, `[scope].deny_folders`, `[mail]` section, `[logging].purged_dir`, `[logging].retention_days` (defined; unused until Phase 1+).
- Mail package scaffold (`src/m365ctl/mail/{catalog,mutate,triage,cli}/`) — empty; filled by Phase 1+.
- `docs/setup/azure-app-registration.md`, `certificate-auth.md`, `first-run.md`, `migrating-from-fazla-od.md`.

### Removed
- Tenant-specific identifiers (UUIDs, cert thumbprint) from all tracked code, tests, and documentation (except the migration note and this CHANGELOG).
