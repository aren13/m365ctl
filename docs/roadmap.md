# Roadmap

Forward-looking work for `m365ctl`. Nothing here is committed; the list captures
direction so contributors can scope ideas without re-deriving them.

For currently-shipped functionality, see [README §Features](../README.md#features)
and the [CHANGELOG](../CHANGELOG.md).

---

## Sibling modules under consideration

Each is a candidate for its own sub-package (`src/m365ctl/<module>/`) following
the OneDrive + Mail pattern. Not scheduled.

- **`m365ctl.calendar`** — events CRUD, free/busy lookups, RSVPs, recurring-series
  expansion. Graph endpoint: `/me/calendar`, `/users/{id}/calendar`. Distinct
  permission scopes from Mail.
- **`m365ctl.contacts`** — address-book CRUD on `/me/contacts` and
  `/me/contactFolders`. Lightweight; mostly a readers + tagging surface.
- **`m365ctl.teams`** — chat + channel ops. Different OAuth scopes
  (`Chat.ReadWrite`, `ChannelMessage.Send`). Throttling profile differs from
  OneDrive/Mail.

## MCP server front-end

Wrap the stable verbs as an [MCP](https://modelcontextprotocol.io) server with
typed tool definitions, so editor-integrated agents can call them directly
instead of shelling out. Sketch:

- **Read-only tools:** `onedrive_search`, `onedrive_inventory`,
  `onedrive_audit_sharing`, `onedrive_download`, `mail_list`, `mail_search`,
  `mail_get`, `mail_settings_get`.
- **Mutating tools:** `onedrive_move`, `onedrive_rename`, `onedrive_copy`,
  `onedrive_label`, `onedrive_clean`, `mail_move`, `mail_delete`, `mail_send`.
  Each with `dry_run: bool = true` and `confirm: bool = false` as typed
  defaults, enforcing the safety envelope structurally rather than relying on
  the host LLM to remember.

The CLI is the supported interface today; an MCP front-end would not replace
it. Wait until the verb surface stabilises (no churn for ~1 month of real use)
before locking it into a typed protocol schema.

## Mail features

- **ML-assisted triage classifier** — distinct from the rule-based DSL; could
  feed suggested rule conditions back to the user. Out of scope for the rule
  engine itself.
- **Quick Steps equivalent** — parameterised multi-action macros built on the
  triage DSL (e.g. "archive + categorise + mark read" as a single named
  invocation).
- **Webhook subscriptions** — Graph `/subscriptions` push notifications would
  give near-real-time triage. Requires a reachable HTTPS endpoint, which is
  out of scope for a CLI; relevant if/when an MCP server lands.
- **Full-text body local index** — current `mail_messages` catalog stores
  metadata + previews only. Full-body indexing (FTS5 / DuckDB FTS) would
  enable richer offline search, but doubles catalog size and `refresh`
  wallclock. Revisit once catalog usage shows real demand.

## OneDrive features

- **Version-history restore** — `od-clean old-versions` is currently
  irreversible; a paired restore command would close the loop.
- **Stale-share re-issue** — `od-clean stale-shares` revokes but does not
  re-create equivalent links.
- **Batched recycle-bin operations** — the PnP.PowerShell helpers drive one
  item per invocation. Bulk recycle-bin workflows would page differently.
- **Cross-tenant `m365ctl undo`** — currently assumes same tenant; importing
  audit logs from an external tenant would unlock disaster-recovery scenarios.
- **True paging for `Find-RecycleBinItem`** — capped at 100 000 per call with
  a stderr warning when hit. PnP has no native page cursor; future enhancement
  would split by `FirstStage` / `SecondStage`.

## Open questions

- **Hard-delete EML retention default** — currently 30 days
  (`[logging].retention_days`), matching the published Graph recycle-bin
  retention. Acceptable as an OSS default; revisit if operators report
  surprise either way.
- **Snooze pattern** — Phase 14 ships the `Deferred/` folder approach as a
  convenience. Defensible but opinionated; an alternative would be Graph's
  scheduled-mailbox API once it stabilises.

---

*Last refreshed: 2026-04-27. Hoisted from internal Phase 0 specs at
`docs/superpowers/specs/` before that tree was deleted ahead of going public.
For execution methodology (TDD, op-log additivity, version bump cadence) see
[AGENTS.md](../AGENTS.md).*
