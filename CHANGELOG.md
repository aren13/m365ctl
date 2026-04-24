# Changelog

All notable changes to m365ctl are documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.6.0] — 2026-04-25

### Added
- **Mail compose (Phase 5a).** Drafts + send + reply + forward + attachment write-side.
  - `m365ctl mail draft {create,update,delete}` — full draft lifecycle. All undoable (draft.create ↔ draft.delete; draft.update restores prior fields; draft.delete recreates from captured body).
  - `m365ctl mail send <draft-id>` — send an existing draft.
  - `m365ctl mail send --new --subject ... --body-file ... --to ...` — inline send. **Blocked when `[mail].drafts_before_send=true` (default)**; set to false in config to enable.
  - `m365ctl mail send --from-plan plan.json --confirm` — bulk send from a plan file. Bulk ≥20 → `/dev/tty` confirm.
  - `m365ctl mail reply <msg-id>` — creates a draft-reply; `--all` for reply-all; `--inline --body "..."` for one-shot send.
  - `m365ctl mail forward <msg-id>` — creates a draft-forward; `--inline --body "..." --to ...` for one-shot send.
  - `m365ctl mail attach add <msg-id> --file <path>` / `remove <msg-id> <att-id>` — small attachments (<3 MB). Large attachments (≥3 MB) detect + defer to Phase 5a-2 with a clear error.
- `src/m365ctl/mail/compose.py` — pure helpers: `parse_recipients`, `build_message_payload`, `count_external_recipients`, `BodyFormatError`.
- 5 new executor modules under `src/m365ctl/mail/mutate/`: `draft.py`, `send.py`, `reply.py`, `forward.py`, `attach.py` (write side — small + remove).
- **`mail send --new` with >20 external recipients → interactive `/dev/tty` confirm** (non-bypassable).
- `bin/mail-draft`, `bin/mail-send`, `bin/mail-reply`, `bin/mail-forward` short wrappers.

### Changed
- `mail/mutate/undo.py`: +5 new reverse-op builders (`mail.draft.{create,update,delete}`, `mail.attach.{add,remove}`); +4 `register_irreversible` calls for `mail.send`, `mail.reply`, `mail.reply.all`, `mail.forward` with operator-facing guidance (e.g. "Sent mail cannot be recalled programmatically").
- `mail/cli/undo.py`: 5 new executor dispatch branches for Phase 5a reversibles.
- `mail/cli/attach.py`: Phase 1's read-only list/get CLI grows `add` + `remove` subcommands.

### Safety
- `--confirm` required for every mutation; dry-run default.
- `mail.send`/`mail.reply*`/`mail.forward` are **irreversible** — clearly surfaced in Dispatcher rejection messages.
- `[mail].drafts_before_send` (default true) blocks `mail send --new` to enforce draft-first review workflow.
- External-recipient TTY confirm on >20 recipients.

### Deferred
- Large attachment upload session (chunked ≥3 MB) → Phase 5a-2.
- Scheduled send (`--schedule-at`) → Phase 5b.
- `internet_message_id` backfill in `after.internet_message_id` → Phase 7 catalog (Graph's 202 response has no body).
- Automatic ETag 412 → refresh → retry loop → Phase 3.5 or later.

## [0.5.0] — 2026-04-25

### Added
- **`m365ctl mail delete` — soft delete via move-to-Deleted-Items.** Single-item (`--message-id --confirm`) or bulk-plan (`--from --subject --folder --plan-out` → review → `--from-plan --confirm`). Bulk ≥20 ops require interactive `/dev/tty` confirm.
- `src/m365ctl/mail/mutate/delete.py` — `execute_soft_delete`: `POST /messages/{id}/move {"destinationId": "deleteditems"}`.
- `bin/mail-delete` short wrapper; dispatcher route for `mail delete` verb.
- `--help` explicitly distinguishes soft delete from the hard-delete `mail clean` verb (Phase 6).

### Changed
- **`m365ctl undo <op-id>` now reverses `mail.delete.soft` ops** — moves the message back to its original parent folder using `before.parent_folder_id` captured at delete time.
- **Closed the Phase 3 `mail.copy` undo chain.** The copy's inverse (`mail.delete.soft` on the new message id) now runs end-to-end: `m365ctl undo <copy-op-id>` soft-deletes the copy instead of printing a Phase 4 deferral message.
- `mail/mutate/undo.py`: `build_reverse_mail_operation` grew a `cmd == "mail-delete-soft"` branch. The Dispatcher's `mail.delete.soft` inverse returns a real `(before, after) → mail.move` spec (replacing the Phase 3 placeholder).
- `mail/cli/undo.py`: the `action == "mail.delete.soft"` branch now calls `execute_soft_delete` (replacing the Phase 3 deferral print).

### Deferred
- Hard delete (`mail clean`) — Phase 6. Uses `DELETE /messages/{id}`; bypasses Deleted Items; irreversible.
- ETag 412 → refresh → retry loop still deferred (Phase 3.5 or later).

## [0.4.0] — 2026-04-25

### Added
- **Safe message mutations (Phase 3).** All undoable.
  - `m365ctl mail move` — single-item (`--message-id --to-folder --confirm`) or bulk plan-file workflow (filter flags + `--to-folder --plan-out plan.json` → review → `--from-plan plan.json --confirm`).
  - `m365ctl mail copy` — same shape as move; creates a new message in the destination folder.
  - `m365ctl mail flag` — `--status flagged|notFlagged|complete` with optional `--start`/`--due`.
  - `m365ctl mail read` — `--yes` / `--no` toggles `isRead`.
  - `m365ctl mail focus` — `--focused` / `--other` sets inferenceClassification.
  - `m365ctl mail categorize` — `--add X` / `--remove X` / `--set X [--set Y]` with add/remove on current categories or set-exact semantics.
- **First mail-side plan-file workflow**: filter flags → `--plan-out plan.json` → `--from-plan plan.json --confirm`. Bulk plans ≥20 items require interactive `/dev/tty` confirm (non-bypassable by piped stdin).
- **All Phase 3 verbs are undoable** via `m365ctl undo <op-id>`:
  - `mail.move` ↔ move back to prior parent folder
  - `mail.flag` ↔ restore prior flag status / start / due
  - `mail.read` ↔ flip `isRead`
  - `mail.focus` ↔ restore prior inferenceClassification
  - `mail.categorize` ↔ restore prior category list
  - `mail.copy` ↔ `mail.delete.soft` on the new message id — **inverse executor lands Phase 4**. For now, the undo CLI prints the new message id and a pointer.
- `GraphClient.patch` + `GraphClient.post` now accept optional `headers={}` for `If-Match: <change_key>` (ETag) plumbing. Executors pass it when `op.args["change_key"]` is set.
- `src/m365ctl/mail/cli/_bulk.py` — `MessageFilter`, `expand_messages_for_pattern`, `emit_plan`, `confirm_bulk_proceed`.
- 6 new `bin/mail-{move,copy,flag,read,focus,categorize}` wrappers and corresponding dispatcher routes.

### Safety
- `--confirm` required for every mutation. Dry-run default.
- `assert_mail_target_allowed` runs before credential construction and Graph (mailbox scope + hardcoded compliance folder deny).
- Bulk ≥20 items → `/dev/tty` confirm (non-bypassable by piped stdin).

### Deferred
- `mail.delete.soft` executor → Phase 4 (first mail message soft-delete verb).
- Automatic ETag 412 → refresh → retry loop → Phase 3.5 or Phase 4 (Phase 3 threads `change_key` into `If-Match` header but surfaces 412 as a GraphError without auto-retry).

## [0.3.0] — 2026-04-24

### Added
- **Mail folder CRUD:** `m365ctl mail folders create/rename/move/delete` (soft delete). Dry-run default; `--confirm` required to execute. Compliance folders (`Recoverable Items`, `Purges`, `Audits`, `Calendar`, `Contacts`, `Tasks`, `Notes`) are hard-coded to reject before any Graph call.
- **Master-category CRUD + sync:** `m365ctl mail categories add/update/remove/sync`. `sync` reconciles against `[mail].categories_master` — only adds missing; never removes user-created extras.
- **Mail undo:** `m365ctl undo <op-id>` now dispatches mail ops alongside `od.*`. The top-level router peeks the audit record's `cmd` field to route.
  - `mail.folder.create` ↔ `mail.folder.delete`
  - `mail.folder.rename` ↔ rename back
  - `mail.folder.move` ↔ move back
  - `mail.folder.delete` — **Irreversible in Phase 2** (folder restore from Deleted Items lands Phase 4+)
  - `mail.categories.add` ↔ `mail.categories.remove`
  - `mail.categories.update` ↔ update back
  - `mail.categories.remove` ↔ `mail.categories.add` (message→category links cannot be restored)
- `src/m365ctl/mail/mutate/` tree: `folders.py`, `categories.py`, `undo.py`, `_common.py` (`MailResult`, `assert_mail_target_allowed`, `derive_mailbox_upn`).
- `src/m365ctl/mail/cli/undo.py` — mail-specific undo handler (routed from top-level `m365ctl undo`).
- Plan-file schema accepts `mail.folder.*` + `mail.categories.*` action namespaces.

### Changed
- `src/m365ctl/mail/cli/folders.py` gains `create/rename/move/delete` subcommands. Bare `mail folders` invocation preserves Phase 1 reader behavior.
- `src/m365ctl/mail/cli/categories.py` gains `add/update/remove/sync` subcommands. Bare invocation preserves list behavior.
- `src/m365ctl/cli/undo.py` rewritten from thin delegate into a cmd-prefix router (OneDrive path unchanged; mail path dispatched to `mail.cli.undo.run_undo_mail`).

### Safety
- Every mail mutation runs `assert_mail_target_allowed` (mailbox scope + hardcoded compliance folder deny) BEFORE credential construction and BEFORE any Graph call.
- `--confirm` required for every mutation. Dry-run is the default.

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
