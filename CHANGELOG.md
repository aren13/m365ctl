# Changelog

All notable changes to m365ctl are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.13.0] — 2026-05-01

### Added
- **Microsoft Graph `$batch` support.** `m365ctl` now uses Graph's `$batch`
  endpoint (≤20 sub-requests per HTTP call) for bulk plan execution and
  read-side fan-out passes. Typical bulk plans (`mail move/delete/copy/
  categorize/flag/read/focus`, `od move/copy/delete/rename`) see ~5×
  speedup. Read-side fan-out improvements include batched first-page GETs
  across mail folders, batched per-tier folder-path resolution, batched
  triage header pre-fetch, batched mail-catalog well-known folder lookups,
  and batched OneDrive tenant-scan user/site enumeration. No CLI flags
  changed; the speedup is transparent.
- New `mail.endpoints.user_base_for_op(op)` shared helper used by mail
  mutate verbs and CLI from-plan paths.
- New `mail.folders.resolve_folder_paths(paths, ...)` for batched
  list-input folder resolution.
- New `mail.attachments.list_attachments_for_messages(...)` primitive for
  batched per-message attachment listings.
- `GraphClient.delete(headers=...)` now accepts conditional-delete headers
  (e.g. `If-Match`).

### Changed
- **Audit-log records during bulk plan execution**: in `--from-plan` mode,
  all `start` records for a phase now appear before any `end` records
  (previously they strictly interleaved per op). `op_id` linkage between
  `start` and `end` is preserved, so `m365ctl undo` and log-replay tools
  are unaffected. Crash safety is maintained: every `start` is durable
  before its corresponding `/$batch` flush.
- Bulk-mode `before` audit state for `mail move` / `mail delete` is now
  populated via batched `?$select=...` GETs rather than full-message
  fetches. As a side effect, `before.parent_folder_path` is no longer
  captured for bulk-moved messages (only `parent_folder_id`); single-item
  `--message-id` mode is unchanged. `m365ctl undo` falls back to the
  folder id for display when path is absent.

## [1.12.2] — 2026-04-27

First-impression polish for `uv tool install m365ctl` users.

### Added
- `m365ctl --version` (and `-V`) prints the installed package version
  via `importlib.metadata`. Previously returned `unknown domain
  '--version'` because the dispatcher routed it as a domain argument.

### Fixed
- `m365ctl od auth whoami` no longer points at `./bin/od-catalog-refresh`
  when the catalog is missing. That path only exists in a source
  checkout; the message now suggests the installed-CLI invocation
  `m365ctl od catalog refresh`.

## [1.12.1] — 2026-04-27

First PyPI release. No code behaviour changes — packaging metadata and
publish-pipeline only.

### Added
- PyPI publishing via Trusted Publishing (OIDC). New
  `.github/workflows/release.yml` builds sdist + wheel with `uv build`
  on every `v*` tag push and uploads via `pypa/gh-action-pypi-publish`.
  The job's tag-vs-pyproject version check refuses mismatched releases.
- `pyproject.toml` metadata for PyPI rendering: `authors`, `license =
  "Apache-2.0"`, `license-files`, `readme`, `keywords`, `classifiers`,
  and `[project.urls]` (Homepage, Repository, Issues, Changelog).

### Changed
- `README.md` install section now leads with `uv tool install m365ctl`
  (and `pipx install m365ctl` / `uv add m365ctl` alternatives) rather
  than git clone. Quickstart switched from `./bin/od-auth login`
  (source-only) to `m365ctl od auth login` (the installed console
  script). The `bin/*` shims remain documented as a from-source
  developer convenience.
- `m365ctl --help` top-level banner replaced the stale
  "Mail = Phase 1+; no verbs yet" line with the real verb-category
  sample for both domains plus a pointer to `m365ctl <domain> --help`.

### Build
- `.gitignore`: exclude `dist/` and `build/` so local `uv build` runs
  don't pollute git status.

## [1.12.0] — 2026-04-27

### Added
- `[scope].internal_domain_pattern` (optional regex) in `config.toml`. When set, `od-audit-sharing` passes it through to `scripts/ps/audit-sharing.ps1` as `-InternalDomainPattern` and the PS script uses it to decide whether each share principal is internal. Default (unset) treats every `@`-bearing principal as external — strictly more conservative than the previous hard-coded behaviour.

### Changed
- **PnP.PowerShell defaults renamed `fazla-od` → `m365ctl`.** The PFX lives at `~/.config/m365ctl/m365ctl.pfx`, the Keychain account is `m365ctl`, and `scripts/ps/convert-cert.sh` writes both. Existing `~/.config/fazla-od/fazla-od.pfx` + Keychain account `fazla-od` continue to work as legacy fallbacks (with a one-line stderr deprecation notice) across `audit-sharing.ps1`, `recycle-purge.ps1`, `recycle-restore.ps1`, `Set-M365ctlLabel.ps1`, and the shared `_M365ctlRecycleHelpers.ps1`. To clean up, see `docs/setup/migrating-from-fazla-od.md` §5.
- `scripts/ps/audit-sharing.ps1` no longer hard-codes the previous tenant-identifying `@fazla\.` regex; the internal-domain decision is configurable via `[scope].internal_domain_pattern`.
- `scripts/ps/Set-M365ctlLabel.ps1` converted from undocumented `M365CTL_TENANT/CLIENT_ID/CERT_PFX/CERT_PFX_PASS` env-var lookup to the parameter-driven pattern used by the other PnP scripts (Tenant + ClientId required; PFX path + Keychain account default to the new `m365ctl` locations with legacy fallbacks). This restores `od-label` apply/remove + their undo paths, which would previously have failed at `Connect-PnPOnline` unless those env vars were pre-exported.
- **API:** `m365ctl.onedrive.mutate.label.execute_label_apply` and `execute_label_remove` now require a keyword-only `cfg: Config` argument so they can pass `-Tenant` / `-ClientId` to the PS script. Both in-tree call sites (`onedrive/cli/label.py`, `onedrive/cli/undo.py`) are updated; external callers must add `cfg=`.
- `od-auth whoami` no longer prints the stale `"Catalog: not yet built (Plan 2)"` placeholder regardless of state. It now reports the catalog file path + size when present, or a `./bin/od-catalog-refresh` hint when missing.

### Documentation
- `docs/ops/pnp-powershell-setup.md` updated to the `m365ctl` defaults with a note pointing to the migration guide for legacy installs.
- `docs/setup/migrating-from-fazla-od.md` gains §5 covering the PFX move + Keychain account rotation.
- `config.toml.example` documents the new `[scope].internal_domain_pattern` field.

## [1.11.1] — 2026-04-26

### Documentation
- Rewrite `README.md` to an open-source standard layout (Highlights, Installation, Quickstart, Features tables, Safety model, Configuration, Documentation index). All internal phase references and version annotations removed from feature descriptions.
- Clean `CHANGELOG.md` headings to Keep a Changelog format ([brackets] + dates), drop "Phase X.y:" prefixes, remove internal session prose, standardize sections, and add GitHub release-tag link references.

### Notes
No code changes. CLI behaviour, public APIs, schemas, and dependencies are identical to 1.11.0.

## [1.11.0] — 2026-04-26

### Added
- `m365ctl.mail.messages.find_message_anywhere` — searches the entire mailbox by `internetMessageId` and returns `(message_id, parent_folder_id)` for the first hit.

### Fixed
- `m365ctl undo <op-id>` for `mail.delete.soft` now handles two cases the original recovery path missed:
  - **Manually moved out of Deleted Items.** If the message was dragged to a different folder between the soft-delete and the undo, the undo locates it via `find_message_anywhere` and restores it from wherever it is. Stderr names the discovered folder.
  - **Already in target folder.** If the user manually dragged it back, the undo short-circuits with an informational notice and exits 0 (no duplicate move).

### Changed
- The undo error message shifts from "may already be hard-deleted or moved manually" to "may already be hard-deleted" — the manual-move case is now handled silently except for the informational stderr line.

## [1.10.0] — 2026-04-26

### Added
- New triage DSL predicate: `headers: { name: <s>, contains|equals|regex: <s>? }`. Matches against `internetMessageHeaders`. Header `name` is matched case-insensitively; with no operator the predicate is an existence check. Operators are evaluated against the header's `value` (case-sensitive `equals` and `regex`, case-insensitive `contains`).
- Lazy per-message header fetch: a Graph GET with `?$select=internetMessageHeaders` is issued only when a `headers` predicate gates the decision and headers are not already cached. Multiple `headers` predicates on the same row share one fetch. Rulesets without `headers` predicates incur zero per-message overhead.

### Example

```yaml
- name: kill-newsletters-with-list-unsubscribe
  match:
    all:
      - folder: Inbox
      - headers: { name: List-Unsubscribe }   # existence check
      - age: { older_than_days: 14 }
  actions:
    - delete: {}
```

### Notes
Headers are fetched lazily rather than catalogued because `internetMessageHeaders` is heavyweight per-message; capturing at crawl time would roughly double `mail catalog refresh` wallclock for the small fraction of users who need header matches. Lazy fetch at triage time has bounded cost (≤1 GET per row in a header-using rule's candidate set).

## [1.9.0] — 2026-04-26

### Performance
- `_drain_delta` now passes `$select` on the first `/messages/delta` call, listing only the ~19 fields `normalize_message` actually reads. The default Graph response is far heavier (body, attachment metadata, ETags, change-keys, sender, replyTo, …); slimming cuts wire payload ~80% and parser work proportionally.
- DuckDB upserts in each round are now wrapped in a single `BEGIN`/`COMMIT` instead of per-statement implicit transactions. At hundreds of rows per round this saves measurable per-call overhead.

### Compatibility
- Same fields persisted to `mail_messages`. Existing catalogs continue to work unchanged. Schema unchanged. CLI unchanged.

### Notes
Subsequent rounds resume from the deltaLink URL, which already encodes the original `$select`; the slim selection is not re-passed.

## [1.8.0] — 2026-04-26

### Added
- `mail export mailbox` now resumes interrupted folders mid-stream. After each successfully exported message the manifest is checkpointed with `last_exported_id` and `last_exported_received_at`; the next run opens the same `.mbox` in append mode and skips messages at or before the cursor.
- Manifest schema bumped to v2 (additive — `last_exported_id` and `last_exported_received_at` per folder). v1 manifests load transparently with the new fields defaulting to `None`.

### Changed
- `export_folder_to_mbox` now returns `(count, last_id, last_ts)` instead of just `count`. CLI callers (`mail export folder`) continue to work unchanged.

### Notes
Cursor comparison is `received_at > last_exported_received_at`, with `message_id == last_exported_id` as the exact-match tie-breaker. ISO-8601 strings sort lexicographically, so the comparison is exact. **Caveat:** if Outlook backfills a message with an older `receivedDateTime` during the pause, that message is skipped — re-run `mail export folder <path>` to capture it.

## [1.7.0] — 2026-04-26

### Added
- New triage DSL predicate: `thread: { has_reply: true|false }`. A conversation is "replied" iff there are ≥ 2 distinct senders in the same `conversation_id` across the candidate row set. No Graph fetches — pure catalog reasoning, computed once per `mail triage run`.
- `evaluate_match(...)` now accepts an optional `context: MatchContext` keyword argument. Existing callers continue to work; `thread` predicates against an empty context evaluate as `False` (defensive default).

### Example

```yaml
- name: follow-up-on-sent
  match:
    all:
      - from: { domain_in: [yourdomain.com] }
      - thread: { has_reply: false }
      - age: { older_than_days: 3 }
  actions:
    - flag: { status: flagged, due_days: 2 }
```

## [1.6.0] — 2026-04-25

### Added
- New triage DSL predicates:
  - `to: { address | address_in | domain_in }` — uses the existing `mail_messages.to_addresses` column.
  - `body: { contains | starts_with | ends_with | regex | equals }` — matches against `mail_messages.body_preview`. **Limitation:** only the preview (first ~256 chars) is matched, not the full body.
  - `cc: { address | address_in | domain_in }` — uses the new `cc_addresses` column.

### Changed
- `mail_messages` schema bumped from v1 to v2: adds a `cc_addresses VARCHAR` column. Migration is non-destructive (`ALTER TABLE … ADD COLUMN IF NOT EXISTS`); existing rows get `NULL` until the next `mail catalog refresh` repopulates them.

## [1.5.0] — 2026-04-25

### Added
- `GraphClient.put_chunk(url, data, *, content_range, content_length)` — unauthenticated PUT to a Graph upload-session URL.
- `m365ctl.mail.mutate.attach.execute_add_attachment_large` — upload-session flow: createUploadSession → streamed PUT chunks → final attachment metadata. Default chunk size 4 MB (a multiple of 320 KB per Graph requirements).
- `mail attach add <msg> --file <≥3MB-file> --confirm` now works end-to-end. Replaces the prior deferred-stub error.

### Notes
The executor reads the file chunk-by-chunk with `Path.open("rb")`, so a 1 GB attachment does not load into memory or bloat the audit log. `args["file_path"]` is recorded; `content_bytes_b64` is omitted for the large path.

## [1.4.0] — 2026-04-25

### Added
- `m365ctl.mail.mutate.send.execute_send_as` — `POST /users/{from_upn}/sendMail` (app-only). Audit records both `effective_sender` (the mailbox being sent as) and `authenticated_principal` (the app `client_id`).
- CLI: `mail sendas <from-upn> --to <addr> ... --subject ... --body ... --confirm`. Bin wrapper `bin/mail-sendas`.
- Out-of-scope `from-UPNs` require `--unsafe-scope` plus a TTY confirmation, reusing the existing `assert_mailbox_allowed` flow.

### Notes
- `mail.send.as` is registered as **irreversible** in the undo dispatcher; `m365ctl undo <op-id>` returns a clear error citing the audit-log compliance fields.

## [1.3.0] — 2026-04-25

### Added
- `m365ctl.mail.mutate.send.execute_send_scheduled` — patches the draft with `singleValueExtendedProperties: PR_DEFERRED_DELIVERY_TIME` and posts `/send`. Outlook holds the message locally until the deliver-at time.
- CLI: `mail send <draft-id> --schedule-at <iso> --confirm`. Gated on `[mail].schedule_send_enabled = true` in `config.toml`.
- Help text documents that delivery depends on the Outlook client being online at the scheduled time.

### Validation
- `--schedule-at` parses ISO-8601 (with `Z` or `+00:00`).
- Scheduled time must be in the future.
- Mutually exclusive with `--new` (only existing drafts can be scheduled).

## [1.2.0] — 2026-04-25

### Added
- `m365ctl.mail.cli._common.derive_mailbox_upn` — canonical helper promoted from three duplicates (catalog/export/triage CLIs).
- `m365ctl.mail.mutate.delegate.{list_delegates, execute_grant, execute_revoke}` and `scripts/ps/Set-MailboxDelegate.ps1` — mailbox delegation via Exchange Online PowerShell. Grant ↔ revoke registered as inverses in the undo dispatcher.
- CLI: `mail delegate {list, grant, revoke}` with `--rights {FullAccess, SendAs, SendOnBehalf}`. Bin wrapper `bin/mail-delegate`.

### Confirmed
- `--mailbox shared:<addr>` routes correctly through every shipped reader and mutator (integration tests cover list/get/search/folders/settings/triage/catalog/export).

### Requires
- PowerShell 7+ on PATH and the `ExchangeOnlineManagement` module (`Install-Module ExchangeOnlineManagement -Scope CurrentUser`) for `mail delegate` actions only. All other verbs continue to use Graph exclusively.

## [1.1.0] — 2026-04-25

### Added
- `m365ctl.mail.mutate.clean.execute_hard_delete` — single-message hard delete with EML capture to `[logging].purged_dir/<YYYY-MM-DD>/<op_id>.eml` **before** the Graph `DELETE`.
- `m365ctl.mail.mutate.clean.execute_empty_folder` and `execute_empty_recycle_bin` — bulk-delete with per-message EML capture to `<purged_dir>/<YYYY-MM-DD>/<op_id>/<message_id>.eml`.
- CLI: `mail clean <message-id>`, `mail clean recycle-bin`, `mail empty <folder-path>` — all require `--confirm` **and** a TTY-typed confirmation phrase. Bin wrappers `bin/mail-clean`, `bin/mail-empty`.

### Safety
- `mail empty` warns on common folder names (Inbox, Sent Items, Drafts, Archive, Outbox) and requires `--unsafe-common-folder` to proceed.
- `mail empty` against ≥ 1000 items requires the operator to type `"YES DELETE N"` (with the exact count) before the wire-delete starts.
- All three actions are registered as **irreversible** in the undo dispatcher; `m365ctl undo <op-id>` returns a clear error pointing at the EML capture path.

### Notes
The captured EMLs are the only recovery path outside Graph. Rotation is governed by `[logging].retention_days` (default 30, matching Graph's recycle-bin retention).

## [1.0.0] — 2026-04-25

First stable release. The CLI surface, audit/undo plumbing, catalog schema, and release process are stable for downstream consumers.

### Added
- `mail digest [--since|--send-to|--limit|--json]` — unread digest builder with text/HTML rendering and optional self-mail through the existing `mail.send` executor.
- `mail archive --older-than-days N --folder PATH [--plan-out|--confirm]` — bulk-move plan into `Archive/<YYYY>/<MM>` with the existing audit/undo path.
- `mail size-report [--top N] [--json]` — catalog-driven per-folder size and count breakdown.
- `mail top-senders [--since|--limit|--json]` — catalog shortcut over `top_senders` query.
- `mail unsubscribe <id> [--method http|mailto|first] [--dry-run|--confirm]` — RFC 2369 / RFC 8058 `List-Unsubscribe` parser with HTTP/mailto dispatch (one-click POST when advertised).
- `mail snooze <id> --until <date|relative> --confirm` and `mail snooze --process --confirm` — `Deferred/<YYYY-MM-DD>` folder + `Snooze/<date>` category convention; `--process` walks due folders and moves messages back to Inbox.
- `docs/mail/convenience-commands.md` — reference for all six convenience verbs.
- Bin wrappers: `mail-digest`, `mail-archive`, `mail-size-report`, `mail-top-senders`, `mail-unsubscribe`, `mail-snooze`.

### Surface area
A complete CLI for Microsoft 365 OneDrive + SharePoint + Mail via Microsoft Graph:
- **OneDrive:** auth, catalog (DuckDB + `/delta`), inventory, search, move/copy/rename/delete (incl. recycle/restore/clean), label, audit-sharing, undo.
- **Mail readers:** auth, whoami, list, get, search, folders, categories, rules, settings, attach.
- **Mail mutators:** move, copy, flag, read, focus, categorize, soft-delete (with undo via rotated-id recovery), draft, send, reply, forward.
- **Mail catalog:** DuckDB mirror via `/delta` with per-folder `--max-rounds` cap.
- **Triage DSL:** YAML rules → match → tagged plan → confirm-execute, reusing all mutate executors.
- **Inbox rules CRUD:** server-side YAML round-trip with full audit/undo.
- **Mailbox settings:** OOO (60-day safety gate + `--force` bypass), signature (local-file fallback), timezone, working hours.
- **Export:** EML, streaming MBOX, attachments, full-mailbox manifest with resume-on-interrupt.
- **Convenience verbs:** digest / archive / unsubscribe / snooze / top-senders / size-report.

### Compatibility
Python 3.11+, tested against Python 3.11, 3.12, and 3.13 on `ubuntu-latest` and `macos-latest`.

### Quality gates
- mypy: 0 errors across the source tree (CI-blocking).
- ruff: clean.
- pytest: 799 passing, 1 live-Graph test gated behind `M365CTL_LIVE_TESTS=1`.

## [0.11.0] — 2026-04-25

### Added
- `m365ctl.mail.export.eml` — per-message EML via Graph `/messages/{id}/$value` (returns native RFC 5322 / MIME bytes).
- `m365ctl.mail.export.mbox` — streaming MBOX writer, per-folder export, `From `-line escaping in bodies.
- `m365ctl.mail.export.attachments` — file-attachment dump with collision suffixes and basename sanitising.
- `m365ctl.mail.export.manifest` and `m365ctl.mail.export.mailbox` — resume-on-interrupt full-mailbox export. `manifest.json` records per-folder status (`pending`/`in_progress`/`done`); re-running picks up where it left off.
- CLI: `mail export {message, folder, mailbox, attachments}` and bin wrapper `bin/mail-export`.

### Notes
Read-only path: no mutations, no audit/undo, no Graph writes.

## [0.10.0] — 2026-04-25

### Added
- `m365ctl.mail.settings.update_mailbox_settings` — generic `/mailboxSettings` PATCH wrapper.
- `m365ctl.mail.mutate.settings` — executors for timezone, working hours, automatic replies (OOO), and local signature. All audit-logged and undoable via `m365ctl undo <op-id>`.
- `m365ctl.mail.signature` — local-file signature module. Content type derived from extension (`.html`/`.htm` → HTML, else text).
- CLI verbs:
  - `mail settings timezone <tz> --confirm`
  - `mail settings working-hours --from-file <yaml> --confirm`
  - `mail ooo {show, on, off}` — automatic-replies management with `--start`/`--end` scheduled-OOO support.
  - `mail signature {show, set}` — read/write the configured signature file.
- Bin wrappers `bin/mail-ooo`, `bin/mail-signature`.

### Safety
- Scheduled-OOO durations longer than 60 days raise `OOOTooLong`; CLI exits 1 with a clear instruction to re-run with `--force`. Catches manual mass-OOO accidents (e.g. `--end` typo'd as `2030`) before they hit the wire.

## [0.9.0] — 2026-04-25

### Added
- `m365ctl.mail.rules.{rule_to_yaml, rule_from_yaml}` — round-trippable YAML ↔ Graph `messageRule` translator. Folder paths resolve bidirectionally via `resolve_folder_path`.
- `m365ctl.mail.mutate.rules` — `execute_{create, update, delete, set_enabled, reorder}` with full audit and undo registration. Each rule op has an inverse, so `m365ctl undo <op-id>` rolls back.
- `mail rules` CLI extended: `create`, `update`, `delete`, `enable`, `disable`, `reorder`, `export`, `import`. `--replace` on `import` first deletes existing rules then re-creates from file.
- `GraphClient.delete()` for HTTP DELETE.

### Round-trip guarantee
`mail rules export --out a.yaml` followed by `mail rules import --from-file a.yaml --replace --confirm` produces a rule set semantically equivalent to the source mailbox (modulo server-assigned IDs).

### Notes
The translator passes through `_unknown_*` for fields it does not model so a Graph-side update does not silently drop data on a round trip.

## [0.8.0] — 2026-04-25

### Added
- `m365ctl.mail.triage.{dsl, match, plan, runner}` — YAML rules → typed `RuleSet` AST → predicate evaluator → tagged `Plan`.
- CLI: `mail triage validate <yaml>` (CI-friendly, no Graph calls) and `mail triage run --rules <yaml> [--plan-out <p> | --confirm]`. Bin wrapper `bin/mail-triage`.
- Three reference rule files in `scripts/mail/rules/` — every example uses `example.com` domains only.
- New runtime dependency: `pyyaml>=6.0`.

### Predicates
`from`, `subject`, `folder`, `age`, `unread`, `is_flagged`, `has_attachments`, `categories`, `focus`, `importance`. Composable with `all` / `any` / `none`.

### Actions
`move`, `copy`, `delete` (soft), `flag`, `read`, `focus`, `categorize` (add/remove/set). Each emitted op carries `args.rule_name` for attribution; existing audit + undo intact.

## [0.7.0] — 2026-04-25

### Added
- `m365ctl.mail.catalog.{schema, db, normalize, crawl, queries}` — DuckDB mirror of mailbox folders + messages, refreshed via Graph `/messages/delta`.
- CLI: `mail catalog refresh` (per-mailbox or `--folder <path>`) and `mail catalog status`. Bin wrappers: `bin/mail-catalog-refresh`, `bin/mail-catalog-status`.
- `mail search --local` now queries the catalog via case-insensitive `LIKE` across subject, from, to, and body-preview.
- `mail whoami` now reports real catalog stats (messages, folders, last refresh).

### Catalog semantics
- Composite primary key `(mailbox_upn, …)` everywhere — multi-mailbox-ready without migration.
- Per-folder delta with stored `delta_link`; `syncStateNotFound` (HTTP 410) triggers a clean full restart, marked `last_status='restarted'`.
- Soft-delete tombstones from `/delta` (`@removed`) become `is_deleted = true` rows; queries exclude them by default.

## [0.6.0] — 2026-04-25

### Added
- **Mail compose surface.** Drafts, send, reply, forward, and the small-attachment write side.
  - `m365ctl mail draft {create, update, delete}` — full draft lifecycle. All undoable.
  - `m365ctl mail send <draft-id>` — send an existing draft.
  - `m365ctl mail send --new --subject ... --body-file ... --to ...` — inline send. **Blocked when `[mail].drafts_before_send=true` (default)**; set to `false` in config to enable.
  - `m365ctl mail send --from-plan plan.json --confirm` — bulk send from a plan file. Bulk ≥ 20 → `/dev/tty` confirm.
  - `m365ctl mail reply <msg-id>` — creates a draft reply; `--all` for reply-all; `--inline --body "..."` for one-shot send.
  - `m365ctl mail forward <msg-id>` — creates a draft forward; `--inline --body "..." --to ...` for one-shot send.
  - `m365ctl mail attach add <msg-id> --file <path>` and `remove <msg-id> <att-id>` — small attachments (< 3 MB).
- `src/m365ctl/mail/compose.py` — pure helpers: `parse_recipients`, `build_message_payload`, `count_external_recipients`, `BodyFormatError`.
- 5 new executor modules under `src/m365ctl/mail/mutate/`: `draft.py`, `send.py`, `reply.py`, `forward.py`, `attach.py` (write side — small + remove).
- `mail send --new` with > 20 external recipients → interactive `/dev/tty` confirm (non-bypassable).
- `bin/mail-draft`, `bin/mail-send`, `bin/mail-reply`, `bin/mail-forward` short wrappers.

### Changed
- `mail/mutate/undo.py`: 5 new reverse-op builders (`mail.draft.{create, update, delete}`, `mail.attach.{add, remove}`); 4 `register_irreversible` calls for `mail.send`, `mail.reply`, `mail.reply.all`, `mail.forward` with operator-facing guidance.
- `mail/cli/undo.py`: 5 new executor dispatch branches.
- `mail/cli/attach.py`: read-only list/get gains `add` and `remove` subcommands.

### Safety
- `--confirm` required for every mutation; dry-run by default.
- `mail.send`/`mail.reply*`/`mail.forward` are **irreversible** — surfaced clearly in dispatcher rejection messages.
- `[mail].drafts_before_send` (default `true`) blocks `mail send --new` to enforce the draft-first review workflow.
- External-recipient TTY confirm on > 20 recipients.

## [0.5.0] — 2026-04-25

### Added
- `m365ctl mail delete` — soft delete via move-to-Deleted-Items. Single-item (`--message-id --confirm`) or bulk-plan (`--from --subject --folder --plan-out` → review → `--from-plan --confirm`). Bulk ≥ 20 ops require interactive `/dev/tty` confirm.
- `src/m365ctl/mail/mutate/delete.py` — `execute_soft_delete`: `POST /messages/{id}/move {"destinationId": "deleteditems"}`.
- `bin/mail-delete` short wrapper; dispatcher route for `mail delete`.

### Changed
- `m365ctl undo <op-id>` now reverses `mail.delete.soft` ops — moves the message back to its original parent folder using `before.parent_folder_id` captured at delete time.
- Closed the `mail.copy` undo chain. The copy's inverse (`mail.delete.soft` on the new message id) now runs end-to-end: `m365ctl undo <copy-op-id>` soft-deletes the copy.

## [0.4.0] — 2026-04-25

### Added
- **Safe message mutations.** All undoable.
  - `m365ctl mail move` — single-item or bulk plan-file workflow.
  - `m365ctl mail copy` — same shape as move; creates a new message in the destination folder.
  - `m365ctl mail flag` — `--status flagged|notFlagged|complete` with optional `--start`/`--due`.
  - `m365ctl mail read` — `--yes` / `--no` toggles `isRead`.
  - `m365ctl mail focus` — `--focused` / `--other` sets `inferenceClassification`.
  - `m365ctl mail categorize` — `--add X` / `--remove X` / `--set X [--set Y]` with add/remove on current categories or set-exact semantics.
- First mail-side plan-file workflow: filter flags → `--plan-out plan.json` → `--from-plan plan.json --confirm`. Bulk plans ≥ 20 items require interactive `/dev/tty` confirm.
- All verbs above are undoable via `m365ctl undo <op-id>`:
  - `mail.move` ↔ move back to prior parent folder
  - `mail.flag` ↔ restore prior flag status / start / due
  - `mail.read` ↔ flip `isRead`
  - `mail.focus` ↔ restore prior `inferenceClassification`
  - `mail.categorize` ↔ restore prior category list
  - `mail.copy` ↔ `mail.delete.soft` on the new message id
- `GraphClient.patch` and `GraphClient.post` accept optional `headers={}` for `If-Match: <change_key>` (ETag) plumbing. Executors pass it when `op.args["change_key"]` is set.
- `src/m365ctl/mail/cli/_bulk.py` — `MessageFilter`, `expand_messages_for_pattern`, `emit_plan`, `confirm_bulk_proceed`.
- 6 new `bin/mail-{move, copy, flag, read, focus, categorize}` wrappers and corresponding dispatcher routes.

### Safety
- `--confirm` required for every mutation. Dry-run by default.
- `assert_mail_target_allowed` runs before credential construction and before any Graph call (mailbox scope + hardcoded compliance folder deny).
- Bulk ≥ 20 items → `/dev/tty` confirm (non-bypassable by piped stdin).

## [0.3.0] — 2026-04-24

### Added
- **Mail folder CRUD:** `m365ctl mail folders create/rename/move/delete` (soft delete). Dry-run default; `--confirm` required to execute. Compliance folders (`Recoverable Items`, `Purges`, `Audits`, `Calendar`, `Contacts`, `Tasks`, `Notes`) are hard-coded to reject before any Graph call.
- **Master-category CRUD + sync:** `m365ctl mail categories add/update/remove/sync`. `sync` reconciles against `[mail].categories_master` — only adds missing; never removes user-created extras.
- **Mail undo:** `m365ctl undo <op-id>` now dispatches mail ops alongside `od.*`. The top-level router peeks the audit record's `cmd` field to route.
  - `mail.folder.create` ↔ `mail.folder.delete`
  - `mail.folder.rename` ↔ rename back
  - `mail.folder.move` ↔ move back
  - `mail.folder.delete` — currently irreversible (folder restore from Deleted Items planned for a later release)
  - `mail.categories.add` ↔ `mail.categories.remove`
  - `mail.categories.update` ↔ update back
  - `mail.categories.remove` ↔ `mail.categories.add` (message-to-category links cannot be restored)
- `src/m365ctl/mail/mutate/` tree: `folders.py`, `categories.py`, `undo.py`, `_common.py` (`MailResult`, `assert_mail_target_allowed`, `derive_mailbox_upn`).
- `src/m365ctl/mail/cli/undo.py` — mail-specific undo handler routed from top-level `m365ctl undo`.
- Plan-file schema accepts `mail.folder.*` and `mail.categories.*` action namespaces.

### Changed
- `src/m365ctl/mail/cli/folders.py` gains `create/rename/move/delete` subcommands. Bare `mail folders` invocation preserves reader behavior.
- `src/m365ctl/mail/cli/categories.py` gains `add/update/remove/sync` subcommands. Bare invocation preserves list behavior.
- `src/m365ctl/cli/undo.py` rewritten from a thin delegate into a `cmd`-prefix router (OneDrive path unchanged; mail path dispatched to `mail.cli.undo.run_undo_mail`).

### Safety
- Every mail mutation runs `assert_mail_target_allowed` (mailbox scope + hardcoded compliance folder deny) **before** credential construction and **before** any Graph call.
- `--confirm` required for every mutation. Dry-run is the default.

## [0.2.0] — 2026-04-24

### Added
- **Mail domain reader surface.**
  - `m365ctl mail list` — OData-filtered message list (`--folder`, `--unread`, `--read`, `--from`, `--subject`, `--since`, `--until`, `--has-attachments`, `--importance`, `--focus`, `--category`, `--limit`, `--json`).
  - `m365ctl mail get` — fetch one message, optionally with body and attachments.
  - `m365ctl mail search` — server-side Graph `/search/query`.
  - `m365ctl mail folders` — tree/flat folder list with counts; hardcoded deny list filters out compliance buckets.
  - `m365ctl mail categories` — master category list.
  - `m365ctl mail rules` — inbox rule list/show.
  - `m365ctl mail settings` — mailbox settings + OOO view.
  - `m365ctl mail attach` — list and get attachments.
  - `m365ctl mail whoami` — identity, declared scopes, delegated probe on `/me/mailFolders/inbox`, cert expiry, catalog stub. Surfaces admin-consent URL on 403.
- `m365ctl.mail.models` — frozen dataclasses with `from_graph_json` parsers: `Message`, `Folder`, `Category`, `Rule`, `Attachment`, `MailboxSettings`, `EmailAddress`, `Body`, `Flag`, `AutomaticRepliesSetting`, `LocaleInfo`, `WorkingHours`.
- `m365ctl.mail.endpoints.user_base(spec, *, auth_mode)` and `parse_mailbox_spec` — `/me` vs `/users/{upn}` routing per mailbox spec.
- `m365ctl.common.safety.assert_mailbox_allowed`, `is_folder_denied`, and `HARDCODED_DENY_FOLDERS` frozenset.
- `GraphClient.get_bytes(path)` — raw byte fetch for attachment content.
- `bin/mail-auth`, `bin/mail-whoami`, `bin/mail-list`, `bin/mail-get`, `bin/mail-search`, `bin/mail-folders`, `bin/mail-categories`, `bin/mail-rules`, `bin/mail-settings`, `bin/mail-attach` — short wrappers.
- `m365ctl mail` top-level route dispatched to the mail sub-package.

### Changed
- `GRAPH_SCOPES_DELEGATED` extended with `Mail.ReadWrite`, `Mail.Send`, `MailboxSettings.ReadWrite`. **Requires admin re-consent** on the Entra app.
- `Message.from_graph_json` now raises `ValueError` (not `assert`) on missing `receivedDateTime` — safe under `python -O`.

### Migration
- Grant admin consent for the three new delegated scopes. Existing 0.1.0 users must re-run `./bin/od-auth login` (or `./bin/mail-auth login`, they share a cache) after consent to pick up the expanded scope set. Until re-consent, delegated mail calls return HTTP 403 with `AccessDenied`; `mail-whoami` surfaces the Entra consent URL automatically.

## [0.1.0] — 2026-04-24

### Changed
- **Breaking:** Renamed package from `fazla_od` to `m365ctl`.
- **Breaking:** Package restructured into `common/`, `onedrive/`, `mail/` sibling sub-packages. See `docs/setup/migrating-from-fazla-od.md`.
- **Breaking:** Config directory moved from `~/.config/fazla-od/` to `~/.config/m365ctl/` (auto-migrated on first run).
- **Breaking:** Keychain items renamed (`FazlaODToolkit:*` → `m365ctl:*`). Users must delete legacy items manually (see migration doc).
- **Breaking:** Environment variable `FAZLA_OD_LIVE_TESTS` renamed to `M365CTL_LIVE_TESTS`. Legacy name accepted with a deprecation warning for one minor version.
- **Breaking:** Plan-file actions now namespaced (`od.move` not `move`). Pre-refactor plans continue to parse via legacy-action normalization.

### Added
- Apache-2.0 LICENSE.
- README quickstart (tenant-agnostic).
- CONTRIBUTING.md.
- GitHub Actions CI: ruff + mypy + pytest (unit and mocked integration) on Python 3.11/3.12/3.13 × Ubuntu/macOS.
- `m365ctl.common.undo.Dispatcher` — domain-agnostic undo registry.
- `m365ctl undo` cross-domain entry point (currently an alias for `m365ctl od undo`).
- Config fields `[scope].allow_mailboxes`, `[scope].deny_folders`, `[mail]` section, `[logging].purged_dir`, `[logging].retention_days` (defined; unused until later releases).
- Mail package scaffold (`src/m365ctl/mail/{catalog, mutate, triage, cli}/`) — empty; filled in subsequent releases.
- `docs/setup/azure-app-registration.md`, `certificate-auth.md`, `first-run.md`, `migrating-from-fazla-od.md`.

### Removed
- Tenant-specific identifiers (UUIDs, cert thumbprint) from all tracked code, tests, and documentation (except the migration note and this changelog).

[Unreleased]: https://github.com/aren13/m365ctl/compare/v1.12.2...HEAD
[1.12.2]: https://github.com/aren13/m365ctl/releases/tag/v1.12.2
[1.12.1]: https://github.com/aren13/m365ctl/releases/tag/v1.12.1
[1.12.0]: https://github.com/aren13/m365ctl/releases/tag/v1.12.0
[1.11.1]: https://github.com/aren13/m365ctl/releases/tag/v1.11.1
[1.11.0]: https://github.com/aren13/m365ctl/releases/tag/v1.11.0
[1.10.0]: https://github.com/aren13/m365ctl/releases/tag/v1.10.0
[1.9.0]: https://github.com/aren13/m365ctl/releases/tag/v1.9.0
[1.8.0]: https://github.com/aren13/m365ctl/releases/tag/v1.8.0
[1.7.0]: https://github.com/aren13/m365ctl/releases/tag/v1.7.0
[1.6.0]: https://github.com/aren13/m365ctl/releases/tag/v1.6.0
[1.5.0]: https://github.com/aren13/m365ctl/releases/tag/v1.5.0
[1.4.0]: https://github.com/aren13/m365ctl/releases/tag/v1.4.0
[1.3.0]: https://github.com/aren13/m365ctl/releases/tag/v1.3.0
[1.2.0]: https://github.com/aren13/m365ctl/releases/tag/v1.2.0
[1.1.0]: https://github.com/aren13/m365ctl/releases/tag/v1.1.0
[1.0.0]: https://github.com/aren13/m365ctl/releases/tag/v1.0.0
[0.11.0]: https://github.com/aren13/m365ctl/releases/tag/v0.11.0
[0.10.0]: https://github.com/aren13/m365ctl/releases/tag/v0.10.0
[0.9.0]: https://github.com/aren13/m365ctl/releases/tag/v0.9.0
[0.8.0]: https://github.com/aren13/m365ctl/releases/tag/v0.8.0
[0.7.0]: https://github.com/aren13/m365ctl/releases/tag/v0.7.0
[0.6.0]: https://github.com/aren13/m365ctl/releases/tag/v0.6.0
[0.5.0]: https://github.com/aren13/m365ctl/releases/tag/v0.5.0
[0.4.0]: https://github.com/aren13/m365ctl/releases/tag/v0.4.0
[0.3.0]: https://github.com/aren13/m365ctl/releases/tag/v0.3.0
[0.2.0]: https://github.com/aren13/m365ctl/releases/tag/v0.2.0
[0.1.0]: https://github.com/aren13/m365ctl/releases/tag/v0.1.0
