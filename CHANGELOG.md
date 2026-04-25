# Changelog

All notable changes to m365ctl are documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## 1.5.0 — Phase 5a-2: chunked attachment upload (≥3 MB)

### Added
- `GraphClient.put_chunk(url, data, *, content_range, content_length)` —
  unauthenticated PUT to a Graph upload-session URL.
- `m365ctl.mail.mutate.attach.execute_add_attachment_large` — three-step
  upload-session flow: createUploadSession → streamed PUT chunks →
  final attachment metadata. Default chunk size 4 MB (multiple of 320 KB
  per Graph requirements).
- `mail attach add <msg> --file <≥3MB-file> --confirm` now works
  end-to-end. Replaces the Phase 5a deferred-stub error.

### Streaming
The executor reads the file chunk-by-chunk with `Path.open("rb")` so a
1 GB attachment doesn't load into memory or bloat the audit log.
`args["file_path"]` is recorded; `content_bytes_b64` is omitted for the
large path.

### Spec parity
This closes the last open item from spec §19. m365ctl 1.5.0 covers the
full spec surface (Phases 0-14, with the documented "out of scope"
items deferred or noted in CHANGELOG).

## 1.4.0 — Phase 13: send-as / on-behalf-of

### Added
- `m365ctl.mail.mutate.send.execute_send_as` — POST `/users/{from_upn}/sendMail` (app-only). Audit records both `effective_sender` (the mailbox being sent as) and `authenticated_principal` (the app `client_id`).
- CLI: `mail sendas <from-upn> --to <addr> ... --subject ... --body ... --confirm`. Bin wrapper `bin/mail-sendas`.
- Out-of-scope from-UPNs require `--unsafe-scope` plus a TTY confirmation, reusing the existing `assert_mailbox_allowed` flow.

### Irreversible
- `mail.send.as` is registered as irreversible in the undo dispatcher; `m365ctl undo <op-id>` returns a clear error citing the audit-log compliance fields.

## 1.3.0 — Phase 5b: scheduled send

### Added
- `m365ctl.mail.mutate.send.execute_send_scheduled` — PATCHes the draft
  with `singleValueExtendedProperties: PR_DEFERRED_DELIVERY_TIME` then
  POSTs `/send`. Outlook holds the message locally until the deliver-at
  time.
- CLI: `mail send <draft-id> --schedule-at <iso> --confirm`. Gated on
  `[mail].schedule_send_enabled = true` in config.toml.
- Help text documents the caveat that delivery depends on the Outlook
  client being online at the scheduled time.

### Validation
- `--schedule-at` parses ISO-8601 (with `Z` or `+00:00`).
- Scheduled time must be in the future.
- Mutually exclusive with `--new` (only existing drafts can be scheduled).

## 1.2.0 — Phase 12: multi-mailbox & delegation

### Added
- `m365ctl.mail.cli._common.derive_mailbox_upn` — canonical helper
  promoted from three duplicates (catalog/export/triage CLIs).
- `m365ctl.mail.mutate.delegate.{list_delegates, execute_grant,
  execute_revoke}` + `scripts/ps/Set-MailboxDelegate.ps1` — mailbox
  delegation via Exchange Online PowerShell. Grant ↔ revoke registered
  as inverses in the undo dispatcher.
- CLI: `mail delegate {list, grant, revoke}` with `--rights {FullAccess,
  SendAs, SendOnBehalf}`. Bin wrapper `bin/mail-delegate`.

### Confirmed
- `--mailbox shared:<addr>` routes correctly through every shipped
  reader and mutator (added integration tests covering list/get/search/
  folders/settings/triage/catalog/export). `user_base` already handled
  this; tests now lock it in.

### Requires
- PowerShell 7+ on PATH and the `ExchangeOnlineManagement` module
  (`Install-Module ExchangeOnlineManagement -Scope CurrentUser`) for
  `mail delegate` actions only. All other verbs continue to use Graph
  exclusively.

## 1.1.0 — Phase 6: hard delete + `mail clean` / `mail empty`

### Added
- `m365ctl.mail.mutate.clean.execute_hard_delete` — single-message hard
  delete with EML capture to `[logging].purged_dir/<YYYY-MM-DD>/<op_id>.eml`
  BEFORE the Graph DELETE.
- `m365ctl.mail.mutate.clean.execute_empty_folder` and
  `execute_empty_recycle_bin` — bulk-delete with per-message EML capture
  to `<purged_dir>/<YYYY-MM-DD>/<op_id>/<message_id>.eml`.
- CLI: `mail clean <message-id>`, `mail clean recycle-bin`,
  `mail empty <folder-path>` — all require `--confirm` AND a TTY-typed
  confirmation phrase. Bin wrappers `bin/mail-clean`, `bin/mail-empty`.

### Safety
- `mail empty` warns on common folder names (Inbox, Sent Items, Drafts,
  Archive, Outbox) and requires `--unsafe-common-folder` to proceed.
- `mail empty` against ≥1000 items requires the operator to type
  `"YES DELETE N"` (with the exact count) before the wire-delete starts.
- All three actions are registered as **irreversible** in the undo
  dispatcher; `m365ctl undo <op-id>` returns a clear error pointing at
  the EML capture path.

### Recovery
The captured EMLs are the only recovery path outside Graph. Rotation is
governed by `[logging].retention_days` (default 30, matching Graph's
recycle-bin retention).

## 1.0.0 — Phase 14: convenience commands → "complete" milestone

m365ctl ships its first stable release. The CLI surface, audit/undo
plumbing, catalog schema, and release process are stable for downstream
consumers.

### Added (Phase 14)
- `mail digest [--since|--send-to|--limit|--json]` — unread digest
  builder with text/HTML rendering and optional self-mail through the
  existing `mail.send` executor.
- `mail archive --older-than-days N --folder PATH [--plan-out|--confirm]`
  — bulk-move plan into `Archive/<YYYY>/<MM>` with the existing
  audit/undo path (one `mail.move` op per qualifying message, tagged
  `rule_name = mail-archive-<YYYYMM>`).
- `mail size-report [--top N] [--json]` — catalog-driven per-folder
  size + count breakdown.
- `mail top-senders [--since|--limit|--json]` — catalog shortcut over
  `top_senders` query, optional time-window filter.
- `mail unsubscribe <id> [--method http|mailto|first] [--dry-run|--confirm]`
  — RFC 2369 / RFC 8058 `List-Unsubscribe` parser + http/mailto
  dispatcher (one-click POST when advertised).
- `mail snooze <id> --until <date|relative> --confirm` and
  `mail snooze --process --confirm` — `Deferred/<YYYY-MM-DD>` folder +
  `Snooze/<date>` category convention; `--process` walks due folders
  and moves messages back to Inbox.
- `docs/mail/convenience-commands.md` — generic-example reference for
  all six verbs.
- Bin wrappers: `mail-digest`, `mail-archive`, `mail-size-report`,
  `mail-top-senders`, `mail-unsubscribe`, `mail-snooze`.

### What 1.0.0 covers
A complete CLI for Microsoft 365 OneDrive + SharePoint + Mail via
Microsoft Graph:
- **OneDrive:** auth, catalog (DuckDB + `/delta`), inventory, search,
  move/copy/rename/delete (incl. recycle/restore/clean), label,
  audit-sharing, undo.
- **Mail readers:** auth, whoami, list, get, search, folders,
  categories, rules, settings, attach.
- **Mail mutators:** move, copy, flag, read, focus, categorize,
  soft-delete (with undo via rotated-id recovery), draft, send,
  reply, forward.
- **Mail catalog:** DuckDB mirror via `/delta` with per-folder
  `--max-rounds` cap.
- **Triage DSL:** YAML rules → match → tagged plan → confirm-execute,
  reusing all mutate executors.
- **Inbox rules CRUD:** server-side YAML round-trip with full
  audit/undo.
- **Mailbox settings:** OOO (60-day safety gate + `--force` bypass),
  signature (local-file fallback), timezone, working hours.
- **Export:** EML, streaming MBOX, attachments, full-mailbox manifest
  with resume-on-interrupt.
- **Convenience verbs:** digest / archive / unsubscribe / snooze /
  top-senders / size-report.

### Out of scope for 1.0
- Phase 5a-2 (chunked attach upload ≥3 MB).
- Phase 5b (scheduled send).
- Phase 6 (hard delete + `mail clean`).
- Phase 12 (multi-mailbox / delegation).
- Phase 13 (send-as / on-behalf-of).
- KQL pushdown for the triage DSL (catalog covers the surface area
  we needed).
- Body / thread / headers predicates in the triage DSL.

All deferred phases sit in the backlog with their dependencies
satisfied; they are 1.x candidates.

### Compatibility
Python 3.11+, tested against Python 3.11 / 3.12 / 3.13 on
ubuntu-latest and macos-latest.

### Quality gates
- mypy: 0 errors across the source tree (CI-blocking since 0.7.x).
- ruff: clean.
- pytest: 799 passing, 1 live-Graph test gated behind
  `M365CTL_LIVE_TESTS=1`.

## 0.11.0 — Phase 11: export (EML, MBOX, attachments)

### Added
- `m365ctl.mail.export.eml` — per-message EML via Graph
  `/messages/{id}/$value` (returns native RFC 5322 / MIME bytes).
- `m365ctl.mail.export.mbox` — streaming MBOX writer + per-folder
  export, `From `-line escaping in bodies.
- `m365ctl.mail.export.attachments` — file-attachment dump with
  collision suffixes and basename sanitising.
- `m365ctl.mail.export.manifest` + `m365ctl.mail.export.mailbox` —
  resume-on-interrupt full-mailbox export. `manifest.json` records
  per-folder status (`pending`/`in_progress`/`done`); re-running picks
  up where it left off.
- CLI: `mail export {message, folder, mailbox, attachments}` and
  bin wrapper `bin/mail-export`.

### Read-only
No mutations, no audit/undo, no Graph writes — pure read path.

### Deferred
- Per-folder mid-stream resume (currently, an interrupted folder
  restarts from scratch on next run).
- Item attachments (`#microsoft.graph.itemAttachment`) and reference
  attachments (OneDrive item links) — Phase 11.x.

## 0.10.0 — Phase 9: mailbox settings (OOO, signature, timezone, working hours)

### Added
- `m365ctl.mail.settings.update_mailbox_settings` — generic /mailboxSettings PATCH wrapper.
- `m365ctl.mail.mutate.settings` — executors for timezone, workingHours, automaticRepliesSetting (OOO), and local signature. All audit-logged + undoable via `m365ctl undo <op-id>`.
- `m365ctl.mail.signature` — local-file signature module. Content type derived from extension (`.html`/`.htm` → HTML, else text).
- CLI verbs:
  - `mail settings timezone <tz> --confirm`
  - `mail settings working-hours --from-file <yaml> --confirm`
  - `mail ooo {show, on, off}` — full automatic-replies management with `--start`/`--end` scheduled-OOO support.
  - `mail signature {show, set}` — read/write the configured signature file.
- Bin wrappers `bin/mail-ooo`, `bin/mail-signature`.

### Safety
- Scheduled-OOO durations longer than 60 days raise `OOOTooLong`; CLI exits 1 with a clear instruction to re-run with `--force`. Manual mass-OOO accidents (e.g. `--end` typo'd as `2030`) caught before they hit the wire.

### Deferred
- Graph roaming-signatures (`/me/userConfiguration` beta) sync — endpoint is unstable; current implementation is local-only with a documented caveat.
- TTY-confirm flow for OOO long-duration override (we ship `--force` instead; cleaner for scripted use).

## 0.9.0 — Phase 8: server-side inbox rules CRUD

### Added
- `m365ctl.mail.rules.{rule_to_yaml,rule_from_yaml}` — round-trippable
  YAML ↔ Graph `messageRule` translator. Folder paths resolve
  bidirectionally via Phase 2's `resolve_folder_path`.
- `m365ctl.mail.mutate.rules` — `execute_{create,update,delete,
  set_enabled,reorder}` with full audit + undo registration. Each rule
  op has an inverse so `m365ctl undo <op-id>` rolls back.
- `mail rules` CLI extended: `create`, `update`, `delete`, `enable`,
  `disable`, `reorder`, `export`, `import`. `--replace` flag on
  `import` first deletes existing rules then re-creates from file.
- `GraphClient.delete()` for HTTP DELETE.

### Round-trip guarantee
`mail rules export --out a.yaml` followed by
`mail rules import --from-file a.yaml --replace --confirm` produces a
rule set semantically equivalent to the source mailbox (modulo
server-assigned ids).

### Deferred (Phase 8.x)
- Graph rule-conditions surface beyond the documented set (e.g. flag
  checks, encryption flags). The translator pass-throughs `_unknown_*`
  for fields it doesn't model so a Graph-side update doesn't silently
  drop data on a round trip.
- `mail rules diff` between mailbox and YAML.

## 0.8.0 — Phase 10: triage DSL + engine

### Added
- `m365ctl.mail.triage.{dsl,match,plan,runner}` — YAML rules → typed
  `RuleSet` AST → predicate evaluator → tagged `Plan`.
- CLI: `mail triage validate <yaml>` (CI-friendly, no Graph calls) and
  `mail triage run --rules <yaml> [--plan-out <p> | --confirm]`. Bin
  wrapper `bin/mail-triage`.
- Three reference rule files in `scripts/mail/rules/` — every example
  uses `example.com` domains only.
- New `pyyaml>=6.0` runtime dependency.

### Predicates shipped
`from`, `subject`, `folder`, `age`, `unread`, `is_flagged`,
`has_attachments`, `categories`, `focus`, `importance`. Composable with
`all` / `any` / `none`.

### Actions shipped
`move`, `copy`, `delete` (soft), `flag`, `read`, `focus`, `categorize`
(add/remove/set). Each emitted op carries `args.rule_name` for
attribution; existing audit + undo intact.

### Deferred
- `to`, `cc`, `body`, `thread`, `headers` predicates — need either Graph
  fetches or richer catalog coverage. Phase 10.x.
- KQL pushdown for "obvious" predicates — Phase 7 catalog covers the
  needed surface area, so the first cut runs entirely local. Phase 10.x.

## 0.7.0 — Phase 7: local mail catalog (DuckDB + /delta)

### Added
- `m365ctl.mail.catalog.{schema,db,normalize,crawl,queries}` — DuckDB mirror
  of mailbox folders + messages, refreshed via Graph `/messages/delta`.
- CLI: `mail catalog refresh` (per-mailbox or `--folder <path>`),
  `mail catalog status`. Bin wrappers: `bin/mail-catalog-refresh`,
  `bin/mail-catalog-status`.
- `mail search --local` now queries the catalog via case-insensitive LIKE
  across subject/from/to/body-preview (the Phase 7 stub is gone).
- `mail whoami` now reports real catalog stats (messages, folders,
  last refresh) instead of the Phase 7 placeholder line.

### Catalog semantics
- Composite PK `(mailbox_upn, …)` everywhere — multi-mailbox-ready for
  Phase 12 delegation without migration.
- Per-folder delta with stored `delta_link`; `syncStateNotFound` (HTTP 410)
  triggers a clean full restart, marked `last_status='restarted'`.
- Soft-delete tombstones from `/delta` (`@removed`) become
  `is_deleted = true` rows; queries exclude them by default.

### Deferred
- `size_estimate` is a placeholder column for now (always 0 from the
  delta crawl). Phase 7.5 / Phase 11 export will backfill it from
  attachment metadata.
- `mail search --hybrid` (Graph + catalog dedupe) — server-side path
  still works; hybrid merging waits for a real demand signal.

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
