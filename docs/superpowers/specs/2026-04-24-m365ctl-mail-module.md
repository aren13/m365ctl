# m365ctl — Admin CLI for Microsoft 365 (OneDrive + Mail)

- **Status:** draft (ready for implementation)
- **Date:** 2026-04-24
- **Owner:** ae (ardaeren13@gmail.com)
- **Project name:** `m365ctl`
- **Source repo (to rename):** `aren13/Fazla-OneDrive` → `aren13/m365ctl`
- **Publishing intent:** open-source, tenant-agnostic. Anyone with admin access to an M365 tenant should be able to clone, configure, and use it without code changes.
- **Parent spec (OneDrive track):** `docs/superpowers/specs/2026-04-24-m365ctl-design.md` (to be renamed per §4; treat its *technical* contents as authoritative — tenant-specific values inside it are examples only)
- **Intended use:** Implementation-ready PRD. Each phase in §19 is executable as its own session via `superpowers:executing-plans` or `superpowers:subagent-driven-development`.

---

## 1. Goal

Ship a single, tenant-agnostic admin CLI — `m365ctl` — that gives terminal control over Microsoft 365 **OneDrive + SharePoint + Mail** via Microsoft Graph. Safe by default (dry-run, plan-file, scope allow-lists, audit + undo), installable against any tenant by editing one config file.

This spec covers three tracks landing in order:

1. **De-branding + restructure track** (§4–5): strip the existing `fazla_od` / "Fazla OneDrive Toolkit" branding, rename the package to `m365ctl`, and restructure so `common/`, `onedrive/`, and `mail/` are sibling sub-packages. Ship as publish-ready for any tenant. No behavior changes to existing OneDrive commands.
2. **Mail track** (§6–18): a complete mail module — read, search, organize, compose, reply, forward, delete, categorize, flag, schedule rules, configure OOO, manage delegations, export. Mail-triage is one workflow built on top (§14), not the product.
3. **Publishing track** (§18, §19 Phase 0 deliverables): LICENSE, README rewrite, CONTRIBUTING, CI, examples-only config, placeholder identifiers. First-time setup for a new tenant should be a README-driven walkthrough, not an archaeology expedition.

## 2. Scope

### In scope

- **Refactor:** `fazla_od` → `m365ctl` package rename + directory reorganization (`common/`, `onedrive/`, `mail/`). Mechanical import rewrites. No behavior changes to existing `od-*` commands. Tests stay green throughout.
- **De-branding:** zero references to "Fazla", "fazla-od", or any tenant-specific identifier in code, docs, examples, or config templates. Keychain labels, env vars, cert directories, CLI entry points — all rebranded. Tenant-specific values (ids, UPNs, site URLs) live only in the user's gitignored `config.toml`.
- **Publishing readiness:** Apache-2.0 LICENSE, rewritten README with quickstart for any tenant, CONTRIBUTING.md, GitHub Actions CI (lint + test matrix), CHANGELOG, documented first-time Azure AD app setup.
- **Mail readers:** list, get, search (server-side `/search/query` + local catalog), conversation/thread reconstruction, attachment enumeration, mailbox folder tree, master categories, inbox rules, mailbox settings.
- **Mail mutations:** move, copy, soft-delete (to Deleted Items), hard-delete (`mail-clean`), flag/unflag, mark read/unread, categorize, focus override.
- **Compose:** create draft, send, reply, reply-all, forward, save-as-draft, update draft, scheduled send (extended property), attachments (small + large upload session).
- **Folders:** list, create, rename, move, delete, empty.
- **Master categories:** list, create, update color/name, delete.
- **Inbox rules (server-side):** list, create, update, delete, reorder, enable/disable.
- **Mailbox settings:** OOO, signature, working hours, timezone, language, delegate access.
- **Multi-mailbox:** delegated access to shared mailboxes, send-as, send-on-behalf-of. App-only targeting of `/users/{upn}/messages`.
- **Export:** per-message `.eml`, per-folder `.mbox`, attachments dump.
- **Local catalog:** DuckDB mirror of mailbox metadata via `/delta`.
- **Triage DSL:** YAML rules-file → plan-file pipeline for bulk categorize/move/flag.
- **Audit + undo:** per-action before/after capture; namespaced dispatcher (`od.*` + `mail.*`) via `m365ctl undo <op-id>`.

### Out of scope (this spec)

- **Calendar, Contacts, Tasks, Teams, OneNote.** Same tenant, different Graph surfaces. Reserved for sibling modules.
- **MCP server wrapping.** Deferred until commands are stable.
- **Web UI.** Never.
- **Automated spam/phish classification.** Rules DSL can act on signals (headers, domain, subject patterns); no ML classifier ships.
- **IMAP/SMTP fallback.** Graph-only. No support for non-M365 mail accounts.
- **Outlook client-side features without Graph representation:** pinned messages, Quick Steps, client-only custom folder colors.
- **Snooze.** Not stable in Graph. Re-implementable as a `mail-defer` folder + dated category pattern (Phase 14).

## 3. Design principles (inherited from parent spec)

Non-negotiable, applied across OneDrive and Mail alike:

1. **Dry-run is the default** for every mutating command. `--confirm` is required to execute.
2. **Bulk destructive ops require a plan file.** Wildcards never go straight to mutation.
3. **Scope allow-list enforced everywhere.** Anything outside requires `--unsafe-scope` + interactive `/dev/tty` confirm.
4. **Deny list is absolute** — items matching `deny_paths` / `deny_folders` never appear in any plan.
5. **Every mutation writes to `logs/ops/YYYY-MM-DD.jsonl`** with before/after.
6. **No hard deletes by default.** Soft-delete routes to recycle bin / Deleted Items; hard-delete is a separate, explicit command.
7. **Rate-limit aware.** Shared retry helper with Retry-After handling.
8. **Undo replays from audit log** where reversible. Irreversible ops are flagged.

These hold regardless of tenant or user. They are the tool's safety envelope, not organizational policy.

## 4. De-branding + restructure track

### 4.1 Motivation

The existing `fazla_od` package was named when the tool had one user and one tenant. Every "Fazla" reference is cosmetic branding — none of the code is semantically tenant-specific. Rename now, before a second domain (mail) doubles the import surface, and before publication compounds the cost.

### 4.2 Rename + de-brand summary

Every mapping below must land in Phase 0. After Phase 0, no `fazla` string appears anywhere in the tree except (a) this spec's migration note, (b) CHANGELOG entry, (c) optional git tag `v0-fazla-final`.

| Before | After | Scope |
|---|---|---|
| Repo: `Fazla-OneDrive` | Repo: `m365ctl` | GitHub rename (preserves redirect) |
| Package: `fazla_od` | Package: `m365ctl` | `src/` tree |
| `pyproject.toml` `name = "fazla-od"` | `name = "m365ctl"` | |
| `pyproject.toml` `[project.scripts] fazla-od = "…"` | `m365ctl = "m365ctl.cli.__main__:main"` | |
| Cert dir: `~/.config/fazla-od/` | `~/.config/m365ctl/` | with auto-migrate from old path on first run |
| Token cache: `~/.config/fazla-od/token_cache.bin` | `~/.config/m365ctl/token_cache.bin` | |
| Keychain: `FazlaODToolkit:DelegatedTokenCache` | `m365ctl:DelegatedTokenCache` | |
| Keychain: `FazlaODToolkit:PfxPassword` | `m365ctl:PfxPassword` | |
| Cert subject example `CN=FazlaODToolkit` | `CN=m365ctl` | self-signed cert generation script updates |
| Env: `FAZLA_OD_LIVE_TESTS` | `M365CTL_LIVE_TESTS` | |
| Binary: `bin/od-undo` | `bin/m365ctl-undo` | cross-domain undo; short alias `m365ctl undo` |
| Doc strings "Fazla M365 tenant", "Fazla OneDrive Toolkit" | "Microsoft 365 tenant", "m365ctl" | README, AGENTS, specs, plans, module docstrings |
| `config.toml.example` tenant_id UUID | `"00000000-0000-0000-0000-000000000000"` placeholder | |
| `config.toml.example` client_id UUID | `"00000000-0000-0000-0000-000000000000"` placeholder | |
| `config.toml.example` allow_drives values | `["me"]` (no site references) | |
| Cert thumbprint in spec | removed or replaced with placeholder | Text: "your cert's SHA-1 thumbprint" |
| Spec filename: `2026-04-24-m365ctl-design.md` | `2026-04-24-m365ctl-design.md` | `git mv` + update all cross-references |

### 4.3 New directory layout

```
m365ctl/
├── LICENSE                            # NEW — Apache-2.0
├── README.md                          # REWRITTEN — tenant-agnostic quickstart
├── CONTRIBUTING.md                    # NEW
├── CHANGELOG.md                       # NEW — 0.1.0 covers Phase 0, subsequent phases each bump minor
├── AGENTS.md                          # rewritten — generic, covers both domains
├── config.toml.example                # generic: placeholder UUIDs, "me" scope, no tenant hints
├── pyproject.toml                     # package name m365ctl; entry m365ctl
├── uv.lock
├── .github/
│   └── workflows/
│       ├── ci.yml                     # lint (ruff) + type (mypy) + test (pytest)
│       └── release.yml                # tagged release → build + PyPI (optional, off by default)
├── bin/
│   ├── od-auth          od-catalog-refresh   od-catalog-status
│   ├── od-search        od-inventory         od-download
│   ├── od-move          od-rename            od-copy
│   ├── od-delete        od-clean             od-label
│   ├── od-audit-sharing od-sync-workspace
│   ├── mail-auth        mail-whoami          mail-catalog-refresh
│   ├── mail-list        mail-get             mail-search
│   ├── mail-folders     mail-categories      mail-rules
│   ├── mail-move        mail-copy            mail-delete
│   ├── mail-flag        mail-read            mail-focus
│   ├── mail-categorize  mail-clean           mail-empty
│   ├── mail-draft       mail-send            mail-reply
│   ├── mail-forward     mail-attach          mail-export
│   ├── mail-settings    mail-ooo             mail-signature
│   ├── mail-delegate    mail-sendas          mail-triage
│   └── m365ctl-undo                   # cross-domain undo
├── scripts/
│   ├── ps/                            # PowerShell admin scripts (od-label, od-audit-sharing)
│   ├── setup/
│   │   ├── create-cert.sh             # NEW — generate self-signed cert with configurable CN
│   │   └── convert-cert.sh            # existing, rebranded
│   └── mail/rules/                    # example triage rules (generic senders, example.com)
├── rclone/
│   └── rclone.conf.example
├── cache/                             # gitignored
│   ├── catalog.duckdb
│   └── mail.duckdb
├── workspaces/                        # gitignored
├── logs/ops/                          # gitignored
├── src/
│   └── m365ctl/
│       ├── __init__.py                # package version
│       ├── __main__.py                # `python -m m365ctl` dispatcher
│       ├── common/
│       │   ├── __init__.py
│       │   ├── auth.py                # MOVED from fazla_od/auth.py
│       │   ├── graph.py               # MOVED
│       │   ├── config.py              # MOVED; extended (§7)
│       │   ├── audit.py               # MOVED
│       │   ├── safety.py              # MOVED; extended (§11)
│       │   ├── retry.py               # MOVED
│       │   ├── planfile.py            # MOVED; namespaced actions
│       │   └── undo.py                # NEW — domain-agnostic dispatcher
│       ├── onedrive/
│       │   ├── __init__.py
│       │   ├── catalog/               # MOVED from fazla_od/catalog/
│       │   ├── download/              # MOVED
│       │   ├── mutate/                # MOVED
│       │   └── cli/                   # MOVED (od-* commands only)
│       ├── mail/                      # NEW — entire tree
│       │   ├── __init__.py
│       │   ├── endpoints.py
│       │   ├── models.py
│       │   ├── messages.py
│       │   ├── folders.py
│       │   ├── categories.py
│       │   ├── rules.py
│       │   ├── settings.py
│       │   ├── attachments.py
│       │   ├── compose.py
│       │   ├── export.py
│       │   ├── catalog/
│       │   │   ├── __init__.py
│       │   │   ├── schema.py
│       │   │   ├── crawl.py
│       │   │   ├── db.py
│       │   │   └── queries.py
│       │   ├── mutate/
│       │   │   ├── __init__.py
│       │   │   ├── move.py
│       │   │   ├── delete.py
│       │   │   ├── clean.py
│       │   │   ├── flag.py
│       │   │   ├── categorize.py
│       │   │   ├── compose.py
│       │   │   └── rules.py
│       │   ├── triage/
│       │   │   ├── __init__.py
│       │   │   ├── dsl.py
│       │   │   ├── match.py
│       │   │   └── plan.py
│       │   └── cli/
│       │       └── *.py               # one module per CLI verb (see §10)
│       └── cli/                       # top-level CLI dispatcher
│           ├── __init__.py
│           ├── __main__.py            # `m365ctl <domain> <verb>`
│           └── undo.py                # `m365ctl undo` — uses common/undo.py
└── docs/
    ├── ops/
    │   └── pnp-powershell-setup.md
    ├── setup/
    │   ├── azure-app-registration.md  # NEW — step-by-step for any tenant
    │   ├── certificate-auth.md        # NEW — generate + upload cert
    │   └── first-run.md               # NEW — from `git clone` to `od-auth whoami`
    └── superpowers/
        ├── specs/
        │   ├── 2026-04-24-m365ctl-design.md          # renamed parent (was m365ctl-design)
        │   └── 2026-04-24-m365ctl-mail-module.md     # this doc
        └── plans/                                    # per-phase plans authored via superpowers:writing-plans
```

### 4.4 Import-rewrite table (Phase 0)

Mechanical pass: `grep -rl 'fazla_od' src/ tests/ bin/ scripts/ docs/` then sed-replace. Manual pass after for sub-path corrections.

| Old import | New import |
|---|---|
| `from fazla_od.config import Config, load_config` | `from m365ctl.common.config import Config, load_config` |
| `from fazla_od.auth import DelegatedCredential, AppOnlyCredential` | `from m365ctl.common.auth import DelegatedCredential, AppOnlyCredential` |
| `from fazla_od.graph import GraphClient, GraphError` | `from m365ctl.common.graph import GraphClient, GraphError` |
| `from fazla_od.audit import AuditLogger, log_mutation_*` | `from m365ctl.common.audit import AuditLogger, log_mutation_*` |
| `from fazla_od.safety import …` | `from m365ctl.common.safety import …` |
| `from fazla_od.retry import with_retry` | `from m365ctl.common.retry import with_retry` |
| `from fazla_od.planfile import …` | `from m365ctl.common.planfile import …` |
| `from fazla_od.catalog.*` | `from m365ctl.onedrive.catalog.*` |
| `from fazla_od.download.*` | `from m365ctl.onedrive.download.*` |
| `from fazla_od.mutate.*` | `from m365ctl.onedrive.mutate.*` |
| `from fazla_od.cli.*` | `from m365ctl.onedrive.cli.*` |

### 4.5 De-branding audit (Phase 0 acceptance gate)

Phase 0 does not complete until these all return empty on the renamed tree:

```bash
grep -rni 'fazla'     src/ tests/ bin/ scripts/ docs/ pyproject.toml README.md AGENTS.md config.toml.example
grep -rni 'fazla_od'  src/ tests/ bin/ scripts/
grep -rni 'FazlaOD'   src/ tests/ bin/ scripts/
grep -rni 'FAZLA_OD'  src/ tests/ bin/ scripts/
grep -rni '361efb70'  .                                   # old tenant UUID
grep -rni 'b22e6fd3'  .                                   # old client UUID
grep -rni 'C38CC9B49D5E4D326B4A79ECAF33CD65B008BCBF' .    # old cert thumbprint
```

Allowed exceptions (explicitly enumerated, reviewed):
- `CHANGELOG.md` entry documenting the rename
- `docs/setup/migrating-from-fazla-od.md` (one-page migration guide for existing users — just me, but good discipline)
- Optional `.github/renamed-from` file for discoverability

### 4.6 Tenant-agnostic configuration

Every tenant-specific value lives in the gitignored `config.toml`. The tracked `config.toml.example` contains only placeholders. Phase 0 rewrites the example file to:

```toml
# m365ctl — configuration template.
# Copy to `config.toml` (gitignored) and fill in for your tenant.
# Setup guide: docs/setup/first-run.md

tenant_id    = "00000000-0000-0000-0000-000000000000"   # Azure AD Directory (tenant) ID
client_id    = "00000000-0000-0000-0000-000000000000"   # App registration (client) ID
cert_path    = "~/.config/m365ctl/m365ctl.key"          # PEM private key, mode 600
cert_public  = "~/.config/m365ctl/m365ctl.cer"          # PEM public cert (uploaded to Entra)
default_auth = "delegated"                              # "delegated" or "app-only"

[scope]
# Drives the toolkit is allowed to touch.
# Forms: "me", "site:<site-id-or-url>", "drive:<drive-id>".
allow_drives         = ["me"]
# Mailboxes the toolkit is allowed to touch.
# Forms: "me", "upn:user@example.com", "shared:team@example.com", "*".
allow_mailboxes      = ["me"]
allow_users          = ["*"]
# OneDrive/SharePoint path globs that are always denied (no override possible).
deny_paths           = []
# Mail folder globs that are always denied (no override possible).
deny_folders         = []
unsafe_requires_flag = true

[catalog]
path             = "cache/catalog.duckdb"
refresh_on_start = false

[mail]
catalog_path           = "cache/mail.duckdb"
default_deleted_folder = "Deleted Items"
default_junk_folder    = "Junk Email"
default_drafts_folder  = "Drafts"
default_sent_folder    = "Sent Items"
default_triage_root    = "Inbox/Triage"
categories_master      = []                             # e.g. ["Followup", "Waiting", "Done"]
signature_path         = ""                             # e.g. "~/.config/m365ctl/signature.html"
drafts_before_send     = true
schedule_send_enabled  = false

[logging]
ops_dir      = "logs/ops"
purged_dir   = "logs/purged"                            # hard-delete EML captures
retention_days = 30
```

No UUIDs, no site names, no email addresses outside `example.com`. Anyone can clone, fill in their IDs, run.

---

## 5. Architecture

### 5.1 Shared vs domain-specific

`m365ctl.common/` (shared — both domains depend on it):
- Auth (MSAL delegated + app-only cert)
- Graph HTTP client (httpx + retry + pagination)
- Config loader
- Audit logger
- Safety / scope primitives
- Retry helper
- Plan-file schema + `new_op_id`
- Undo dispatcher

`m365ctl.onedrive/` (OneDrive/SharePoint domain — existing code, relocated):
- Catalog (DuckDB over file metadata)
- Download (Graph streaming)
- Mutations (move, rename, copy, delete, clean, label)
- CLI (`od-*` verbs)

`m365ctl.mail/` (mail domain — new):
- Readers (messages, folders, categories, rules, settings, attachments)
- Mutations (move, copy, delete-soft, clean-hard, flag, categorize, compose, rules)
- Catalog (DuckDB, `/delta`-based)
- Triage DSL
- Export (EML / MBOX / attachments)
- CLI (`mail-*` verbs)

### 5.2 Separate catalogs

OneDrive catalog (`cache/catalog.duckdb`) tracks files. Mail catalog (`cache/mail.duckdb`) tracks messages + folders. Both DuckDB for tool-stack uniformity, separate files for decoupled invalidation. No cross-domain joins.

### 5.3 Namespaced undo dispatcher

`m365ctl.common.undo.Dispatcher` keys on `<domain>.<verb>`:

```python
Dispatcher.register("od.move",         m365ctl.onedrive.mutate.move.inverse)
Dispatcher.register("od.rename",       m365ctl.onedrive.mutate.rename.inverse)
Dispatcher.register("mail.move",       m365ctl.mail.mutate.move.inverse)
Dispatcher.register("mail.delete.soft", m365ctl.mail.mutate.delete.inverse_soft)
Dispatcher.register("mail.flag",       m365ctl.mail.mutate.flag.inverse)
Dispatcher.register("mail.categorize", m365ctl.mail.mutate.categorize.inverse)
# …all reversible mail verbs (§12)
# Irreversible verbs register a sentinel that raises with operator guidance:
Dispatcher.register_irreversible("mail.send",          "Sent mail cannot be recalled programmatically.")
Dispatcher.register_irreversible("mail.delete.hard",   "Hard-delete is irreversible; see logs/purged/<op_id>.eml for the captured message.")
Dispatcher.register_irreversible("mail.clean.recycle-bin", "Recycle-bin purge is irreversible.")
```

Reads legacy (pre-namespace) `logs/ops/*.jsonl` entries: any bare action `move`/`rename`/`copy`/`delete` is treated as `od.*`, preserving undo for ops created before Phase 0.

### 5.4 CLI entry points

```
m365ctl od search "invoice"                     # → m365ctl.onedrive.cli.search.main()
m365ctl mail list --folder Inbox --unread       # → m365ctl.mail.cli.list.main()
m365ctl undo <op-id> --confirm                  # → m365ctl.cli.undo.main()
```

Short wrapper binaries (`bin/od-search`, `bin/mail-list`, `bin/m365ctl-undo`) remain as 3-line bash shims delegating to `python -m m365ctl …`. Preserves existing muscle memory; nothing to learn.

### 5.5 Tenant-agnostic design

No tenant identifier, user UPN, site URL, or environment-specific value exists in code. Every scope, allow-list, and default is read from `config.toml` at runtime. The tool does not know its tenant until it reads config. This is both the de-branding invariant and the open-source contract.

The tool **never** writes the tenant identifier to the audit log in a way that assumes its shape — tenant id is opaque string from config, logged verbatim.

---

## 6. Authentication (Graph scopes)

### 6.1 Scopes to request

Adds to `m365ctl.common.auth.GRAPH_SCOPES_DELEGATED`:

- `Mail.ReadWrite` — list/get/search/move/delete/flag/categorize
- `Mail.Send` — send/reply/forward
- `MailboxSettings.ReadWrite` — OOO, signature, working hours

Adds to Application permissions in Entra:

- `Mail.ReadWrite` (app-only)
- `Mail.Send` (app-only)
- `MailboxSettings.ReadWrite` (app-only)

All require admin consent. `m365ctl mail whoami` surfaces missing scopes with a link to Entra.

### 6.2 Send-as / on-behalf-of

- **Delegated send-on-behalf:** requires target mailbox's owner to grant "Send on Behalf" via Exchange (`Set-Mailbox -GrantSendOnBehalfTo`). Shipped in Phase 13 via PnP.PowerShell.
- **App-only send-as:** `Mail.Send` application scope permits sending as any tenant mailbox. Guarded by `allow_mailboxes`.

### 6.3 First-run setup (docs/setup/azure-app-registration.md)

Documented in Phase 0. Covers:
1. Create Azure AD app registration.
2. Add API permissions (Delegated + Application).
3. Generate self-signed cert (`scripts/setup/create-cert.sh`).
4. Upload cert to Entra.
5. Admin-consent.
6. Fill in `config.toml`.
7. Run `od-auth login` → `od-auth whoami`.

Target: a new user goes from clone to working `whoami` in < 20 minutes.

---

## 7. Configuration schema

### 7.1 Full example

See §4.6 for the rendered `config.toml.example`. Every value either has a safe default (e.g. `allow_drives = ["me"]`) or a placeholder UUID that forces the user to replace it.

### 7.2 Dataclass additions in `m365ctl.common.config`

```python
@dataclass(frozen=True)
class ScopeConfig:
    allow_drives: list[str]
    allow_mailboxes: list[str]                     # NEW
    allow_users: list[str] = field(default_factory=lambda: ["*"])
    deny_paths: list[str] = field(default_factory=list)
    deny_folders: list[str] = field(default_factory=list)   # NEW
    unsafe_requires_flag: bool = True

@dataclass(frozen=True)
class MailConfig:                                  # NEW
    catalog_path: Path
    default_deleted_folder: str = "Deleted Items"
    default_junk_folder: str = "Junk Email"
    default_drafts_folder: str = "Drafts"
    default_sent_folder: str = "Sent Items"
    default_triage_root: str = "Inbox/Triage"
    categories_master: list[str] = field(default_factory=list)
    signature_path: Path | None = None
    drafts_before_send: bool = True
    schedule_send_enabled: bool = False

@dataclass(frozen=True)
class LoggingConfig:
    ops_dir: Path
    purged_dir: Path                               # NEW
    retention_days: int = 30                       # NEW

@dataclass(frozen=True)
class Config:
    tenant_id: str
    client_id: str
    cert_path: Path
    cert_public: Path
    default_auth: AuthMode
    scope: ScopeConfig
    catalog: CatalogConfig                         # OneDrive
    mail: MailConfig                               # NEW
    logging: LoggingConfig
```

---

## 8. Data model (Python dataclasses)

All in `m365ctl.mail.models`. Mirrored to DuckDB columns in `m365ctl.mail.catalog.schema`.

```python
@dataclass(frozen=True)
class Message:
    id: str
    mailbox_upn: str
    internet_message_id: str
    conversation_id: str
    conversation_index: bytes
    parent_folder_id: str
    parent_folder_path: str
    subject: str
    sender: EmailAddress
    from_addr: EmailAddress
    to: list[EmailAddress]
    cc: list[EmailAddress]
    bcc: list[EmailAddress]
    reply_to: list[EmailAddress]
    received_at: datetime
    sent_at: datetime | None
    is_read: bool
    is_draft: bool
    has_attachments: bool
    importance: str                                # low / normal / high
    flag: Flag
    categories: list[str]
    inference_classification: str                  # focused / other
    body_preview: str
    body: Body | None                              # loaded on demand
    web_link: str
    change_key: str

@dataclass(frozen=True)
class EmailAddress:
    name: str
    address: str

@dataclass(frozen=True)
class Body:
    content_type: Literal["text", "html"]
    content: str

@dataclass(frozen=True)
class Flag:
    status: Literal["notFlagged", "flagged", "complete"]
    start_at: datetime | None = None
    due_at: datetime | None = None
    completed_at: datetime | None = None

@dataclass(frozen=True)
class Folder:
    id: str
    mailbox_upn: str
    display_name: str
    parent_id: str | None
    path: str                                      # "/Inbox/Triage/Waiting"
    total_items: int
    unread_items: int
    child_folder_count: int
    well_known_name: str | None                    # "inbox", "drafts", …

@dataclass(frozen=True)
class Category:
    id: str
    display_name: str
    color: str                                     # "preset0" … "preset24"

@dataclass(frozen=True)
class Rule:
    id: str
    display_name: str
    sequence: int
    is_enabled: bool
    has_error: bool
    is_read_only: bool
    conditions: dict
    actions: dict
    exceptions: dict

@dataclass(frozen=True)
class MailboxSettings:
    timezone: str
    language: LocaleInfo
    working_hours: WorkingHours
    auto_reply: AutomaticRepliesSetting
    delegate_meeting_message_delivery: str
    date_format: str
    time_format: str

@dataclass(frozen=True)
class AutomaticRepliesSetting:
    status: Literal["disabled", "alwaysEnabled", "scheduled"]
    external_audience: Literal["none", "contactsOnly", "all"]
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    internal_reply_message: str
    external_reply_message: str

@dataclass(frozen=True)
class Attachment:
    id: str
    message_id: str
    kind: Literal["file", "item", "reference"]
    name: str
    content_type: str
    size: int
    is_inline: bool
    content_id: str | None
```

---

## 9. Graph API surface (reference tables)

`{ub}` = user-base, resolves to `/me` (delegated) or `/users/{upn}` (app-only).

### 9.1 Readers

| CLI | Method | Path |
|---|---|---|
| `mail-list` | GET | `{ub}/mailFolders/{id}/messages?$top=…&$filter=…&$orderby=…` |
| `mail-get <id>` | GET | `{ub}/messages/{id}?$expand=attachments` |
| `mail-search <q>` | POST | `/search/query` with `entityTypes:["message"]` |
| `mail-search --local` | SQL | DuckDB `mail_messages` |
| `mail-folders` | GET | `{ub}/mailFolders?$top=200&includeHiddenFolders=true` (recursive) |
| `mail-categories` | GET | `{ub}/outlook/masterCategories` |
| `mail-rules` | GET | `{ub}/mailFolders/inbox/messageRules` |
| `mail-settings` | GET | `{ub}/mailboxSettings` |
| `mail-ooo` | GET | `{ub}/mailboxSettings/automaticRepliesSetting` |
| `mail-signature` | local/beta | see §12.7 |
| `mail-attach list <msg>` | GET | `{ub}/messages/{id}/attachments` |
| `mail-attach get <msg> <att>` | GET | `{ub}/messages/{id}/attachments/{aid}/$value` |

### 9.2 Mutations

| CLI | Method | Path |
|---|---|---|
| `mail-move` | POST | `{ub}/messages/{id}/move` `{destinationId}` |
| `mail-copy` | POST | `{ub}/messages/{id}/copy` `{destinationId}` |
| `mail-delete` (soft) | POST | `{ub}/messages/{id}/move` → Deleted Items |
| `mail-clean <msg>` (hard) | DELETE | `{ub}/messages/{id}` |
| `mail-clean recycle-bin` | custom | empty Deleted Items |
| `mail-empty <folder>` | DELETE per-msg | all messages in folder |
| `mail-flag` | PATCH | `{ub}/messages/{id}` `{flag: {…}}` |
| `mail-read [yes\|no]` | PATCH | `{ub}/messages/{id}` `{isRead: bool}` |
| `mail-focus [focused\|other]` | PATCH | `{ub}/messages/{id}` `{inferenceClassification: …}` |
| `mail-categorize` | PATCH | `{ub}/messages/{id}` `{categories: […]}` |
| `mail-categories add` | POST | `{ub}/outlook/masterCategories` |
| `mail-categories update` | PATCH | `{ub}/outlook/masterCategories/{id}` |
| `mail-categories remove` | DELETE | `{ub}/outlook/masterCategories/{id}` |
| `mail-folders create` | POST | `{ub}/mailFolders` (or `.../{id}/childFolders`) |
| `mail-folders rename` | PATCH | `{ub}/mailFolders/{id}` `{displayName}` |
| `mail-folders move` | POST | `{ub}/mailFolders/{id}/move` |
| `mail-folders delete` | DELETE | `{ub}/mailFolders/{id}` |
| `mail-rules create` | POST | `{ub}/mailFolders/inbox/messageRules` |
| `mail-rules update` | PATCH | `{ub}/mailFolders/inbox/messageRules/{id}` |
| `mail-rules delete` | DELETE | `{ub}/mailFolders/inbox/messageRules/{id}` |
| `mail-draft create` | POST | `{ub}/messages` |
| `mail-draft update` | PATCH | `{ub}/messages/{id}` |
| `mail-send <draft>` | POST | `{ub}/messages/{id}/send` |
| `mail-send --new` | POST | `{ub}/sendMail` |
| `mail-reply <msg>` | POST | `{ub}/messages/{id}/reply` or `/createReply` |
| `mail-reply --all <msg>` | POST | `{ub}/messages/{id}/replyAll` |
| `mail-forward <msg>` | POST | `{ub}/messages/{id}/forward` or `/createForward` |
| `mail-attach add` | POST | `{ub}/messages/{id}/attachments` (or upload session) |
| `mail-attach remove` | DELETE | `{ub}/messages/{id}/attachments/{aid}` |
| `mail-ooo set` | PATCH | `{ub}/mailboxSettings` |
| `mail-signature set` | local / beta | user roaming signature (§12.7) |
| `mail-delegate grant` | PowerShell | `Set-Mailbox -GrantSendOnBehalfTo` |
| `mail-sendas <upn>` | varies | app-only + scope guard |

### 9.3 Sync

| CLI | Method | Path |
|---|---|---|
| `mail-catalog-refresh` | GET | `{ub}/mailFolders/{id}/messages/delta` |

---

## 10. CLI surface

Shared flags per parent spec: `--scope`, `--json`, `--dry-run`, `--confirm`, `--plan-out`, `--from-plan`, `--unsafe-scope`, `--limit`, `--page-size`. Mail-specific shared:

- `--mailbox me | upn:<addr> | shared:<addr>` (defaults `me`; app-only requires explicit)
- `--folder <path>` (resolves to folder id)
- `--unread` / `--read` filters

### 10.1 Auth + catalog

| Command | Notes |
|---|---|
| `mail-auth login` | Delegated device-code; same token cache as `od-auth` |
| `mail-whoami` | Identity, scopes, mailbox access summary, catalog stats |
| `mail-catalog-refresh --mailbox <> [--folder <>]` | Delta-sync |
| `mail-catalog-status` | Catalog summary |

### 10.2 Read

| Command | Notes |
|---|---|
| `mail-list` | `--folder`, `--from`, `--to`, `--subject`, `--since`, `--until`, `--unread`, `--has-attachments`, `--importance`, `--focus focused\|other`, `--category`, `--limit`, `--json` |
| `mail-get <message-id>` | `--with-body`, `--with-headers`, `--with-attachments`, `--eml` |
| `mail-search <query>` | Server-side `/search/query`. `--local` hits catalog. Hybrid by default. |
| `mail-thread <message-id>` | Walks `conversationId` chronologically + tree view |
| `mail-folders [<path>]` | `--tree`, `--with-counts`, `--include-hidden` |
| `mail-categories` | Master list |
| `mail-rules` | Ordered by sequence |
| `mail-attach list <msg>` / `get <msg> <att> [--out <path>]` | |

### 10.3 Organize

| Command | Notes |
|---|---|
| `mail-move` | Single `--message-id`. Bulk `--pattern … --plan-out …` then `--from-plan … --confirm`. |
| `mail-copy` | Same shape as `mail-move`. |
| `mail-delete` | Soft delete (→ Deleted Items). Reversible via `m365ctl undo`. |
| `mail-clean <message-id>` | Hard delete. `--unsafe-scope` + plan required. Not undoable. |
| `mail-clean recycle-bin` | Purge Deleted Items. TTY confirm. |
| `mail-empty <folder> [--keep-subfolders]` | Delete all messages in folder. Separate command. |
| `mail-flag --message-id … [--status flagged\|complete\|notFlagged] [--due …]` | Bulk via plan. |
| `mail-read --message-id … [--yes\|--no]` | Bulk via plan. |
| `mail-focus --message-id … [--focused\|--other]` | Bulk via plan. |
| `mail-categorize --message-id … [--add …] [--remove …] [--set …]` | Bulk via plan. |

### 10.4 Folders

| Command | Notes |
|---|---|
| `mail-folders create <parent-path> <name>` | |
| `mail-folders rename <path> <new-name>` | Undoable |
| `mail-folders move <path> <new-parent-path>` | Undoable |
| `mail-folders delete <path>` | Soft; `--purge` + `--unsafe-scope` for hard |

### 10.5 Categories master

| Command | Notes |
|---|---|
| `mail-categories list` | |
| `mail-categories add <name> --color preset0..preset24` | |
| `mail-categories update <id> [--name …] [--color …]` | |
| `mail-categories remove <id>` | Warns if messages carry it (queries catalog) |
| `mail-categories sync` | Reconcile with `[mail].categories_master` config |

### 10.6 Inbox rules

| Command | Notes |
|---|---|
| `mail-rules list [--disabled]` | |
| `mail-rules show <id>` | |
| `mail-rules create --from-file rule.yaml` | YAML schema in §15 |
| `mail-rules update <id> --from-file rule.yaml` | |
| `mail-rules delete <id>` | Undoable |
| `mail-rules enable <id>` / `disable <id>` | |
| `mail-rules reorder <id> --sequence N` | Undoable |
| `mail-rules export` / `import` | Backup/restore |

### 10.7 Compose

| Command | Notes |
|---|---|
| `mail-draft create --to … --subject … [--body-file …] [--cc …] [--bcc …] [--importance …]` | |
| `mail-draft update <id> [--subject …] [--body-file …] [--to …] [--cc …]` | |
| `mail-draft delete <id>` | |
| `mail-send <draft-id> --confirm` | |
| `mail-send --new --to … --subject … --body-file … --confirm` | Blocked if `drafts_before_send=true` |
| `mail-send --from-plan plans/send.json --confirm` | Batch |
| `mail-send --schedule-at "<iso>" --confirm` | Phase 5b |
| `mail-reply <message-id> --body-file …` | Creates draft-reply |
| `mail-reply --all <message-id> --body-file …` | |
| `mail-reply --inline <message-id> --body "<short>" --confirm` | Create + send |
| `mail-forward <message-id> --to … --body-file …` | |
| `mail-attach add <message-id> --file <path>` | <3MB inline; ≥3MB upload session |
| `mail-attach remove <message-id> <att-id>` | |

### 10.8 Mailbox settings

| Command | Notes |
|---|---|
| `mail-settings show` | |
| `mail-ooo on --internal "<msg>" --external "<msg>" [--audience all\|contactsOnly\|none] [--start …] [--end …]` | |
| `mail-ooo off` | |
| `mail-signature show` / `set --file <path>` | §12.7 caveats |
| `mail-settings timezone <tz>` | |
| `mail-settings working-hours --days Mon,Tue,… --start 09:00 --end 17:00` | |

### 10.9 Delegation & send-as

| Command | Notes |
|---|---|
| `mail-delegate list` | PS backend |
| `mail-delegate grant <upn> --send-on-behalf [--send-as] [--full-access]` | |
| `mail-delegate revoke <upn>` | |
| `mail-sendas <upn> --new --to … --subject … --body-file … --confirm` | App-only; scope-guarded |

### 10.10 Export

| Command | Notes |
|---|---|
| `mail-export message <message-id> --out <path.eml>` | |
| `mail-export folder <path> --out <path.mbox>` | |
| `mail-export mailbox --out <dir>` | One mbox per folder + manifest.json |
| `mail-export --from-plan plans/export.json` | |
| `mail-export attachments <message-id> --out-dir <path>` | |

### 10.11 Triage

| Command | Notes |
|---|---|
| `mail-triage run --rules <file.yaml> [--plan-out …]` | |
| `mail-triage run --from-plan … --confirm` | |
| `mail-triage validate <file.yaml>` | No Graph calls |

### 10.12 Undo

| Command | Notes |
|---|---|
| `m365ctl undo <op-id> [--confirm]` | Works for `od.*` and `mail.*` alike |
| `m365ctl undo --list [--since …] [--cmd …]` | List undoable ops |
| `m365ctl-undo …` | Short-binary alias |

---

## 11. Safety & scope model (mail additions)

### 11.1 `allow_mailboxes` forms

| Form | Meaning | Auth mode |
|---|---|---|
| `"me"` | Signed-in user | Delegated |
| `"upn:user@example.com"` | Specific mailbox | App-only, or delegated with delegation |
| `"shared:team@example.com"` | Shared mailbox | Either |
| `"*"` | Any mailbox | App-only only; `--unsafe-scope` required on mutations |

### 11.2 `deny_folders` enforcement

Glob against `parent_folder_path`. Matches dropped from plan generation. Cannot be overridden.

Hardcoded always-denied (not user-configurable):
- `Recoverable Items/*` — hidden compliance folder
- `Purges/*` — hold folder
- `Audits/*` — audit logs
- `Calendar/*`, `Contacts/*`, `Tasks/*`, `Notes/*` — out of scope

### 11.3 Interactive TTY confirms

Required (not bypassable by piped stdin) for:
- Any `--unsafe-scope` operation
- `mail-clean` (hard delete), even with `--confirm`
- `mail-empty <folder>` with ≥1000 messages
- `mail-send` with >20 external recipients
- `mail-rules delete`
- `mail-delegate grant --full-access`
- `mail-ooo on` with duration > 60 days

### 11.4 Plan-file action namespace

```
mail.move                mail.copy
mail.delete.soft         mail.delete.hard
mail.clean.recycle-bin   mail.empty
mail.flag                mail.read
mail.focus               mail.categorize
mail.folder.create       mail.folder.rename
mail.folder.move         mail.folder.delete
mail.categories.add      mail.categories.update
mail.categories.remove
mail.draft.create        mail.draft.update
mail.draft.delete
mail.send                mail.reply
mail.reply.all           mail.forward
mail.attach.add          mail.attach.remove
mail.rules.create        mail.rules.update
mail.rules.delete        mail.rules.enable
mail.rules.disable       mail.rules.reorder
mail.settings.update     mail.ooo.set
mail.signature.set
mail.delegate.grant      mail.delegate.revoke
mail.sendas.send
mail.export.message      mail.export.folder
mail.export.mailbox
```

OneDrive actions use `od.*` prefix (set by Phase 0 refactor).

---

## 12. Audit + undo (mail actions)

### 12.1 Per-action capture table

| Action | `before` captures | `after` captures | Inverse op |
|---|---|---|---|
| `mail.move` | `parent_folder_id`, `parent_folder_path` | new `parent_folder_id` | `mail.move` back |
| `mail.copy` | — | new `message_id` | `mail.delete.soft` on the copy |
| `mail.delete.soft` | `parent_folder_id`, `parent_folder_path` | moved to Deleted Items | `mail.move` from Deleted Items → prior |
| `mail.delete.hard` | `internet_message_id`, `subject`, `sender.address`, full EML → `logs/purged/<op_id>.eml` | — | **not undoable** |
| `mail.clean.recycle-bin` | count | — | not undoable |
| `mail.empty` | count, folder path | — | not undoable |
| `mail.flag` | prior `flag.{status, dueDateTime, startDateTime}` | new flag | `mail.flag` with prior |
| `mail.read` | prior `isRead` | new `isRead` | `mail.read` with prior |
| `mail.focus` | prior `inferenceClassification` | new | `mail.focus` with prior |
| `mail.categorize` | prior `categories[]` | new | `mail.categorize --set <prior>` |
| `mail.folder.create` | — | new folder id, path | `mail.folder.delete` (soft) |
| `mail.folder.rename` | prior `displayName` | new | rename back |
| `mail.folder.move` | prior `parentFolderId` | new | move back |
| `mail.folder.delete` | folder metadata | — | move from Deleted Items back (if recoverable) |
| `mail.categories.add` | — | new category id | `mail.categories.remove` |
| `mail.categories.update` | prior `{displayName, color}` | new | update with prior |
| `mail.categories.remove` | full category + list of messages carrying it | — | `mail.categories.add` (links lost) |
| `mail.draft.create` | — | new message id | `mail.draft.delete` |
| `mail.draft.update` | full prior draft | new | update with prior |
| `mail.draft.delete` | full draft | — | `mail.draft.create` from captured |
| `mail.send` | — | `internet_message_id`, `sent_at` | **not undoable**; audit only |
| `mail.reply` / `reply.all` / `forward` | — | outgoing message id | not undoable |
| `mail.attach.add` | — | new attachment id | `mail.attach.remove` |
| `mail.attach.remove` | full attachment bytes | — | `mail.attach.add` from captured |
| `mail.rules.create` | — | new rule id, full rule | `mail.rules.delete` |
| `mail.rules.update` | full prior rule | new | update with prior |
| `mail.rules.delete` | full prior rule | — | `mail.rules.create` from captured |
| `mail.rules.enable` / `disable` | prior `isEnabled` | new | flip |
| `mail.rules.reorder` | prior `sequence` | new | reorder back |
| `mail.settings.update` | full prior settings | new | update with prior |
| `mail.ooo.set` | full prior auto-reply | new | set with prior |
| `mail.signature.set` | prior signature bytes | new | set with prior |
| `mail.delegate.grant` | — | grant record | `mail.delegate.revoke` |
| `mail.delegate.revoke` | prior grant | — | `mail.delegate.grant` with prior |
| `mail.sendas.send` | — | outgoing message id | not undoable |
| `mail.export.*` | — | output path | manual (delete file) |

### 12.2 "Purged" EML capture

Hard-delete (`mail.delete.hard`) and `mail.empty` write full EML to `logs/purged/<YYYY-MM-DD>/<op_id>.eml` **before** the Graph DELETE. Last-resort recovery outside Graph. Rotation governed by `[logging].retention_days` (default 30).

### 12.3 Undo dispatcher

`m365ctl.common.undo.Dispatcher.run(op_id)`:

1. Read `logs/ops/*.jsonl`; find `op_id`.
2. Look up `action` in registry.
3. If unregistered → `IrreversibleOp(action, reason)`.
4. Build inverse `Operation` from `before`.
5. Execute.
6. Log as new op with `action=<orig>.inverse`, `parent_op_id=<orig>`.

Tolerates legacy bare actions by prefixing `od.` on read.

---

## 13. Error handling

### 13.1 Mail-specific Graph errors

Retry helper handles 429/503/5xx with `Retry-After`. Non-retriable mail errors:

| Code | Meaning | Handling |
|---|---|---|
| `ErrorItemNotFound` | Deleted between plan & execute | Skip, log, continue batch |
| `ErrorAccessDenied` | Scope / delegation missing | Fail fast with guidance |
| `ErrorMessageSizeExceeded` | Attachment >150 MB | Fail fast; direct to upload session |
| `ErrorQuotaExceeded` | Mailbox full | Fail fast |
| `ErrorMailboxStoreUnavailable` | Transient | Retry once |
| `ErrorRecipientNotResolved` | Invalid recipient | Fail fast (send) |
| `MailboxConcurrency` (ETag) | Concurrent modification | Refresh + retry once |

### 13.2 Idempotency

Mail ops are mostly idempotent (same-state PATCH is no-op). Exceptions:
- `send` — executor records `internet_message_id` in `after` before marking op complete; plan re-runs skip sent ops.
- `attach add` — executor hashes file bytes + name + content-id; skips existing matches.

### 13.3 ETag / change-key

Every mutation sends `If-Match: <changeKey>`. 412 → refresh + retry once. Second 412 aborts.

---

## 14. Triage workflow

### 14.1 Rules-file DSL (Phase 11)

```yaml
version: 1
mailbox: me
rules:
  - name: archive-newsletters
    enabled: true
    match:
      all:
        - from:
            domain_in: [example-newsletter.com, another-news.com]
        - age:
            older_than_days: 7
        - folder: Inbox
    actions:
      - categorize:
          add: [Archived/Newsletter]
      - move:
          to_folder: Archive/Newsletters

  - name: follow-up-on-sent
    match:
      all:
        - from: me
        - thread: { has_reply: false }
        - age: { older_than_days: 3 }
    actions:
      - flag: { status: flagged, due_days: 2 }
      - categorize: { add: [Triage/Waiting] }

  - name: urgent-from-leadership
    match:
      all:
        - unread: true
        - folder: Inbox
        - from: { address_in: [alice@example.com, bob@example.com] }
    actions:
      - categorize: { add: [Triage/Followup] }
      - focus: focused
```

Example files use `example.com` / `example-newsletter.com` domains only. No tenant-specific examples.

### 14.2 Predicates

`from`, `to`, `cc`, `subject`, `body`, `folder`, `age`, `importance`, `has_attachments`, `size`, `is_read`, `is_flagged`, `categories`, `focus`, `thread`, `headers`.

Operators: `equals`, `contains`, `starts_with`, `ends_with`, `regex`, `in`, `domain_in`, `address_in`, `older_than_days`, `newer_than_days`, `between`.

### 14.3 Actions

`move`, `copy`, `delete` (soft), `flag`, `unflag`, `read`, `unread`, `focus`, `categorize` (`add|remove|set`), `forward`.

### 14.4 Evaluation

- **Hybrid matcher.** KQL-compatible predicates (`from:`, `subject:`, `older_than:`, `folder:`, `has:attachments`) push to `/search/query`. Remaining run locally.
- **Deterministic ordering.** Rules top-to-bottom; multiple matches stack ops in plan.
- **Dry-run first.** `mail-triage run --rules r.yaml --plan-out plan.json` never mutates.
- **Rule attribution.** Every op carries `args.rule_name`.

### 14.5 `mail-triage validate`

Parse + shape-check + unknown-predicate detection. No Graph calls. CI-friendly.

---

## 15. Inbox rules schema (`mail-rules create --from-file`)

Server-side rule YAML (translated to Graph `messageRule`):

```yaml
display_name: auto-archive-newsletters
sequence: 10
enabled: true
conditions:
  from_addresses:
    - name: "Example Newsletter"
      address: "noreply@example-newsletter.com"
  subject_contains: ["weekly digest"]
  sender_contains: ["@example-newsletter.com"]
actions:
  move_to_folder: "Archive/Newsletters"
  mark_as_read: true
  stop_processing_rules: true
exceptions:
  from_addresses:
    - address: "boss@example.com"
```

CLI translates folder names ↔ folder ids.

---

## 16. Testing strategy

### 16.1 Layers

| Layer | Scope | When |
|---|---|---|
| Unit | DSL parser, plan-file I/O, match evaluator, inverse builders | Every phase |
| Graph mocked | Each `mutate/*.py`, each reader | Phase that ships it |
| Graph cassettes | Core readers + mutations | After every phase |
| Live smoke | End-to-end against a test mailbox | Before marking phase done |

### 16.2 Live smoke

Any M365 mailbox the user owns for testing (documented as "set `MAIL_SMOKE_UPN=<your-test-upn@example.com>` to enable"). Populate with distinctive subject-tags; tests assert pre- / post-op state and run inverse to verify restoration. Gated by `M365CTL_LIVE_TESTS=1`.

### 16.3 Scope-violation tests

Every mutation has a test attempting the op against an out-of-scope mailbox; asserts `ScopeViolation` with no Graph call issued.

### 16.4 CI matrix

GitHub Actions (Phase 0):
- Python 3.11, 3.12, 3.13
- Ubuntu + macOS
- Steps: ruff lint → mypy → pytest (unit + mocked integration)
- Live smoke is **never** run in CI — requires a real tenant.

---

## 17. Logging & observability

### 17.1 Op log

`logs/ops/YYYY-MM-DD.jsonl`:

```json
{"ts":"…","phase":"start","op_id":"01H…","cmd":"mail-move","action":"mail.move",
 "mailbox_upn":"me","message_id":"AAMk…","args":{…},"before":{…}}
{"ts":"…","phase":"end","op_id":"01H…","result":"ok","after":{…}}
```

### 17.2 `mail-whoami` / `od-auth whoami` / `m365ctl whoami`

Surfaces:
- Delegated identity + scopes
- App-only availability (cert expiry with `⚠` inside 30 days)
- Configured `allow_drives` + `allow_mailboxes`
- Catalog stats (OneDrive + mail): last sync, message/item counts, disk size
- Last 10 op_ids with status
- Count of undoable ops in last 7 days
- Missing/required Graph permissions (with link to Entra admin-consent page)

---

## 18. Publishing & open-source readiness (Phase 0 deliverables)

### 18.1 LICENSE

Apache-2.0. Chosen for explicit patent grant + enterprise-friendliness. File at repo root.

### 18.2 README rewrite

Required sections:
1. What it is (1 paragraph — "admin CLI for Microsoft 365 OneDrive + Mail via Graph")
2. Status badge (CI), Python version badge, license badge
3. Feature list (bulleted, both domains)
4. Quickstart:
   ```
   git clone https://github.com/<you>/m365ctl
   cd m365ctl
   uv sync
   cp config.toml.example config.toml
   # Follow docs/setup/first-run.md to fill in
   ./bin/od-auth login
   ./bin/od-auth whoami
   ```
5. Links to `docs/setup/azure-app-registration.md` and `docs/setup/first-run.md`
6. Safety model summary (dry-run default, plan-file workflow, scope allow-lists, undo)
7. Command reference index (links to per-command docs)
8. Contributing link
9. License link

### 18.3 CONTRIBUTING.md

- Dev setup (`uv sync`, `pre-commit install`)
- Test commands (unit, mocked integration, live smoke gate)
- Code style (ruff + mypy strict)
- Commit message conventions
- PR checklist (tests green, de-branding audit clean, new command has docs entry)

### 18.4 docs/setup/

- `azure-app-registration.md` — screenshots-optional walkthrough: create app, add permissions, admin-consent.
- `certificate-auth.md` — `scripts/setup/create-cert.sh <CN>` generates + prints upload instructions.
- `first-run.md` — end-to-end from clone to green `whoami`.

### 18.5 CI (`.github/workflows/ci.yml`)

```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest]
        python: ["3.11", "3.12", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v2
      - run: uv sync --all-extras
      - run: uv run ruff check
      - run: uv run mypy src
      - run: uv run pytest -m "not live"
```

### 18.6 CHANGELOG.md

```markdown
## [Unreleased]

## [0.1.0] — 2026-04-24
### Changed
- **Breaking:** Renamed package from `fazla_od` to `m365ctl`.
- **Breaking:** Package restructured into `common/`, `onedrive/`, `mail/` sibling sub-packages. See migration notes in `docs/setup/migrating-from-fazla-od.md`.
- **Breaking:** Config directory moved from `~/.config/fazla-od/` to `~/.config/m365ctl/` (auto-migrated on first run).
- **Breaking:** Keychain items renamed (`FazlaODToolkit:*` → `m365ctl:*`).
- **Breaking:** Environment variable `FAZLA_OD_LIVE_TESTS` renamed to `M365CTL_LIVE_TESTS`.
### Added
- Apache-2.0 LICENSE, README quickstart, CONTRIBUTING, GitHub Actions CI.
- `m365ctl undo` cross-domain undo dispatcher.
```

Subsequent phases each bump minor and add entries.

### 18.7 `docs/setup/migrating-from-fazla-od.md`

One page; covers:
- `mv ~/.config/fazla-od ~/.config/m365ctl` (or let auto-migrate run)
- Keychain items: `security delete-generic-password -s FazlaODToolkit` + re-login
- Rename any local `config.toml` hardcoded paths
- Legacy `logs/ops/*.jsonl` op_ids remain undoable

### 18.8 Version pinning

- `pyproject.toml` `version = "0.1.0"` at end of Phase 0.
- Each subsequent phase bumps minor: 0.2.0 (mail readers), 0.3.0 (folders/categories), … 0.15.0 (convenience commands).

### 18.9 What `m365ctl` explicitly does not claim

- **Not official.** README states: "This is an independent open-source project. Not affiliated with Microsoft."
- **No warranty** (Apache-2.0 §7).
- **Multi-tenant aware but untested at scale.** Works against any single tenant at a time via `config.toml`; simultaneous multi-tenant use is not a supported configuration.

---

## 19. Implementation plans — phase breakdown

Each phase below is an independent session. Strictly ordered unless noted parallel-safe.

### Phase 0 — De-brand, rename, restructure, publish-ready

**Goal:** Ship the package rename, directory restructure, and open-source readiness in one coherent session. All existing `od-*` tests green. Zero "Fazla" references remain. Repo is clone-and-go for any tenant.

**Deliverables:**

Part A — Rename & restructure:
- `pyproject.toml`: `name = "m365ctl"`; `[project.scripts] m365ctl = "m365ctl.cli.__main__:main"`; update metadata (description, authors if missing, URLs).
- `git mv src/fazla_od src/m365ctl`; split into `common/`, `onedrive/` per §4.3.
- Scaffold `src/m365ctl/mail/` tree with empty `__init__.py` in every subdir (Phase 1 fills it).
- Mechanical import rewrite (§4.4) across `src/`, `tests/`, `bin/`, `scripts/`.
- Extract domain-agnostic undo into `m365ctl.common.undo.Dispatcher`.
- Register all existing `od.*` actions; legacy bare actions auto-prefix on read.
- Rename `bin/od-undo` → `bin/m365ctl-undo`; leave `bin/od-undo` as deprecated alias printing a 1-line notice.
- Update all `bin/od-*` wrappers to `python -m m365ctl od <verb>`.
- Action-namespace the plan-file emitter (`od.move` not `move`).

Part B — De-branding (enforces §4.5 audit):
- Rename cert dir: `~/.config/fazla-od/` → `~/.config/m365ctl/`. Auto-migrate in `m365ctl.common.auth._load_persistent_cache` on first run (detect legacy path, `mv` with confirm, fall back to re-login if mv fails).
- Rename Keychain items: `security delete-generic-password -s FazlaODToolkit`; new items auto-created on next login.
- Rename env var: `FAZLA_OD_LIVE_TESTS` → `M365CTL_LIVE_TESTS`. Accept both during a 1-minor-version deprecation window; log warning on legacy.
- Remove tenant UUIDs, client UUIDs, cert thumbprint from ALL tracked files. Replace with placeholders (§4.6).
- Rewrite docstrings: every `"""…fazla_od…"""` / `"""…Fazla M365 tenant…"""` → generic.
- Run the §4.5 grep suite; must all return empty (except explicit allowed exceptions).
- Rename `docs/superpowers/specs/2026-04-24-m365ctl-design.md` → `2026-04-24-m365ctl-design.md`; update all cross-references.
- Copy this spec (FAZLA-MAIL-SPEC.md from the user's workspace) into the repo as `docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md`; update its own cross-references (removing "Fazla" from this doc's own title at the same time).

Part C — Publishing readiness (§18):
- Add `LICENSE` (Apache-2.0).
- Rewrite `README.md` per §18.2.
- Write `CONTRIBUTING.md` per §18.3.
- Write `docs/setup/azure-app-registration.md`, `docs/setup/certificate-auth.md`, `docs/setup/first-run.md`.
- Write `docs/setup/migrating-from-fazla-od.md` (one page; for me).
- Add `scripts/setup/create-cert.sh` (argument: CN; defaults to `m365ctl`).
- Add `.github/workflows/ci.yml` (lint + type + unit + mocked integration; live smoke explicitly excluded).
- Add `CHANGELOG.md` with 0.1.0 entry (§18.6).
- Set `pyproject.toml` `version = "0.1.0"`.
- Rename the config example (§4.6) to be fully placeholder-based.

Part D — Config extension for mail:
- `[scope]` gains `allow_mailboxes`, `deny_folders`.
- `[mail]` section added (§4.6).
- `[logging]` gains `purged_dir`, `retention_days`.
- `m365ctl.common.config` dataclasses updated (§7.2).
- Phase 0 ships these fields as **unused** (no mail code yet). Phase 1 starts using them.

Part E — GitHub rename:
- Rename `Fazla-OneDrive` → `m365ctl` via GitHub settings (preserves redirect).
- Update any local git remotes.
- Update badges in README to the new URL.

**Acceptance (hard gates):**
- `uv run pytest -m "not live"` green on every matrix cell.
- §4.5 grep suite all empty (except documented exceptions).
- `./bin/od-auth whoami` still works against a real tenant (user-run live check — not in CI).
- `./bin/od-inventory --top-by-size 10` still works.
- `./bin/m365ctl-undo <past-op-id> --confirm` works on an op-log entry written pre-refactor.
- `python -c "import m365ctl; import m365ctl.common.auth; import m365ctl.common.graph; import m365ctl.onedrive.cli; import m365ctl.mail"` succeeds.
- A clean clone + `uv sync` + `cp config.toml.example config.toml` + `vim config.toml` (fill tenant/client IDs, cert paths) + `./bin/od-auth login` + `./bin/od-auth whoami` → green, ≤20 min for someone following `docs/setup/first-run.md`.
- CHANGELOG 0.1.0 entry committed.
- GitHub repo renamed; CI green on main.

**Dependencies:** none.

**Risks:**
- Mechanical import rewrite misses a dynamic import. Mitigation: `grep -rn 'fazla_od'` sweep + full test run + import-smoke script.
- Keychain / cert-dir auto-migration fails on macOS permission quirks. Mitigation: fall back to "no legacy found, please re-run `m365ctl auth login`".
- Spec/plan cross-references break. Mitigation: `grep -rn 'm365ctl-design'` after renames.

**Parallel-safe:** no. Phase 0 is the foundation; everything else waits.

---

### Phase 1 — Mail readers + auth scope expansion

**Goal:** Read-only mail surface: list, get, search, folders, categories, rules, settings, attachments. No writes.

**Deliverables:**
- `m365ctl.common.auth`: add `Mail.ReadWrite`, `Mail.Send`, `MailboxSettings.ReadWrite` to `GRAPH_SCOPES_DELEGATED`; admin-consent re-granted in Entra (manual step — `docs/setup/azure-app-registration.md` updated to include mail perms).
- `m365ctl.mail.endpoints.user_base(mailbox, auth_mode) -> str` with delegated / app-only routing.
- `m365ctl.mail.models`: dataclasses from §8.
- `m365ctl.mail.messages`: `list_messages`, `get_message`, `search_messages_graph`, `get_thread`.
- `m365ctl.mail.folders`: `list_folders` (recursive), `resolve_folder_path`, `get_folder`.
- `m365ctl.mail.categories`: `list_master_categories`.
- `m365ctl.mail.rules`: read-only — `list_rules`, `get_rule`.
- `m365ctl.mail.settings`: read-only — `get_settings`, `get_auto_reply`.
- `m365ctl.mail.attachments`: read-only — `list_attachments`, `get_attachment`.
- `m365ctl.mail.cli.{list, get, search, folders, categories, rules, settings, attach}`: readers with `--json`.
- `bin/mail-list`, `bin/mail-get`, `bin/mail-search`, `bin/mail-folders`, `bin/mail-categories`, `bin/mail-rules`, `bin/mail-settings`, `bin/mail-attach`.
- `bin/mail-auth`, `bin/mail-whoami` (delegates to `m365ctl auth` / `m365ctl whoami`; short aliases).
- `m365ctl.common.safety.assert_mailbox_allowed(mailbox_upn, cfg)`.
- Tests: unit on parsers, mocked Graph integration, live smoke gated by `M365CTL_LIVE_TESTS=1`.
- Bump version to 0.2.0; CHANGELOG entry.

**Acceptance:**
- `mail-folders --tree` prints full folder tree.
- `mail-list --folder Inbox --unread --limit 10 --json` returns 10 unread.
- `mail-get <id> --with-body --with-attachments --json` returns full Message.
- `mail-search "from:alice AND subject:meeting"` returns hits.
- `mail-categories`, `mail-rules`, `mail-settings` all print correctly.
- `mail-whoami` surfaces mailbox access + scope presence (and surfaces missing scope with remediation URL when `Mail.ReadWrite` not consented).
- Scope-violation test: listing a mailbox not in `allow_mailboxes` fails fast.

**Dependencies:** Phase 0.

**Risks:** Scope not admin-consented → 403. Mitigation: `mail-whoami` detects and prints Entra admin-consent URL.

**Parallel-safe:** no.

---

### Phase 2 — Folder CRUD + categories master CRUD

**Goal:** Low-risk mutations: folder create/rename/move/delete (soft), master categories add/update/remove/sync.

**Deliverables:**
- `m365ctl.mail.mutate.folders`: `create_folder`, `rename_folder`, `move_folder`, `delete_folder` (soft), with `before` / `after` capture.
- `m365ctl.mail.mutate.categories`: add/update/remove + `sync` against `[mail].categories_master`.
- `m365ctl.common.planfile`: register `mail.folder.*` and `mail.categories.*` action namespaces.
- `m365ctl.common.undo.Dispatcher`: register inverses.
- CLI: `mail-folders {create,rename,move,delete}`, `mail-categories {add,update,remove,sync}`.
- Tests: unit + mocked + live smoke (create temp folder → rename → delete → undo → verify restored).
- Bump to 0.3.0.

**Acceptance:**
- `mail-folders create Inbox Triage --confirm` creates `/Inbox/Triage`.
- `mail-folders rename Inbox/Triage Triaged --confirm` renames; `m365ctl undo <op> --confirm` reverses.
- `mail-categories sync --confirm` reconciles to config.
- Plan-file bulk workflow works for folder moves.
- Hardcoded deny-folder test: attempts on `Recoverable Items` blocked with no Graph call.

**Dependencies:** Phase 1.

**Parallel-safe:** no.

---

### Phase 3 — Safe mutations: move, copy, flag, read, focus, categorize

**Goal:** Bread-and-butter message mutations. All undoable.

**Deliverables:**
- `m365ctl.mail.mutate.{move, flag, categorize}` (execute + inverse).
- `m365ctl.mail.mutate.copy` (execute; inverse = `mail.delete.soft` on the copy).
- CLI: `mail-move`, `mail-copy`, `mail-flag`, `mail-read`, `mail-focus`, `mail-categorize`.
- Plan-file bulk via `--pattern` / `--plan-out` / `--from-plan --confirm`.
- `m365ctl.mail.cli._common`: pattern expansion (glob over subjects/folders/senders), scope filtering, interactive confirm on >N items.
- Tests: unit, mocked, live smoke per verb with inverse.
- Bump to 0.4.0.

**Acceptance:**
- Single-item and bulk plan workflows for each verb.
- `m365ctl undo` restores prior state verbatim for each.
- ETag mismatch → single retry → clean succeed or fail.
- Scope-violation test per verb.

**Dependencies:** Phase 2.

**Parallel-safe:** no.

---

### Phase 4 — Soft-delete + restore

**Goal:** `mail-delete` → Deleted Items; `m365ctl undo` restores.

**Deliverables:**
- `m365ctl.mail.mutate.delete.execute_soft_delete` / `inverse_soft_delete`.
- CLI: `mail-delete`.
- Plan schema: `action: mail.delete.soft`.
- Explicit distinction from `mail-clean` in `--help`: "for hard delete see `mail-clean`".
- Tests: unit + live (delete → verify in Deleted Items → undo → verify restored).
- Bump to 0.5.0.

**Acceptance:**
- `mail-delete --message-id … --confirm` moves to Deleted Items.
- `m365ctl undo <op> --confirm` restores.
- Bulk via plan works.
- If target message manually purged from Deleted Items between op and undo → clean "not found" error.

**Dependencies:** Phase 3.

**Parallel-safe:** yes (with Phase 5 — different code paths).

---

### Phase 5a — Drafts + send + reply + forward (core compose)

**Goal:** Compose mail. Drafts-first default.

**Deliverables:**
- `m365ctl.mail.compose`: `create_draft`, `update_draft`, `send_draft`, `send_new`, `create_reply`, `create_reply_all`, `create_forward`, `send_reply_inline`.
- `m365ctl.mail.attachments`: write side — `add_attachment_small`, `add_attachment_large` (upload session), `remove_attachment`.
- CLI: `mail-draft {create,update,delete}`, `mail-send`, `mail-reply`, `mail-forward`, `mail-attach {add,remove}`.
- `--body-file` preferred; `--body` inline with multiline warning.
- `mail-send` requires `--confirm`; TTY prompt if >20 external recipients.
- All compose actions register as `IrreversibleOp` in dispatcher.
- `mail.send` op records `internet_message_id` in `after`; plan re-runs skip sent ops.
- Tests: unit, mocked, live smoke (send to self, verify delivery).
- Bump to 0.6.0.

**Acceptance:**
- Draft create/update/delete round-trip.
- `mail-send <draft-id> --confirm` delivers.
- `mail-reply --inline <msg> --body "ok" --confirm` works as one-step.
- `mail-attach add <msg> --file 10mb.bin` uses upload session.
- Plan-based send idempotency test.

**Dependencies:** Phase 1.

**Parallel-safe:** yes (with Phase 4).

---

### Phase 5b — Scheduled send (optional)

**Goal:** Deferred delivery via `singleValueExtendedProperties` `PR_DEFERRED_DELIVERY_TIME`.

**Deliverables:**
- `m365ctl.mail.compose.send_scheduled(draft_id, deliver_at)`.
- CLI: `mail-send --schedule-at "<iso>"`; enabled only if `[mail].schedule_send_enabled=true`.
- Help text documents caveat: depends on Outlook client online at `deliver_at`.
- Bump to 0.7.0.

**Dependencies:** Phase 5a. **Parallel-safe:** yes.

---

### Phase 6 — Hard delete + empty folder (`mail-clean`)

**Goal:** Irreversible deletions with heavy guardrails.

**Deliverables:**
- `m365ctl.mail.mutate.clean.execute_hard_delete` — EML dump to `logs/purged/` **before** DELETE.
- `m365ctl.mail.mutate.clean.execute_empty_recycle_bin`, `execute_empty_folder` (≥1000 items → TTY).
- CLI: `mail-clean <message-id>`, `mail-clean recycle-bin`, `mail-empty <folder>`.
- Irreversible dispatcher registration with EML path in error message.
- `mail-clean` always requires TTY confirm even with `--confirm`.
- `mail-empty` warns on common folder names (Inbox, Sent Items).
- `--help` opens with "This is NOT `mail-delete`."
- Tests: live smoke on disposable test folder.
- Bump to 0.8.0.

**Dependencies:** Phase 4. **Parallel-safe:** yes.

---

### Phase 7 — Local mail catalog

**Goal:** Fast offline queries via DuckDB + `/delta` sync.

**Deliverables:**
- `m365ctl.mail.catalog.schema`: `mail_messages`, `mail_folders`, `mail_deltas`, `mail_categories`.
- `m365ctl.mail.catalog.crawl`: per-folder delta; first call full, subsequent incremental.
- `m365ctl.mail.catalog.db`: connection helper → `[mail].catalog_path`.
- `m365ctl.mail.catalog.queries`: `unread_in_folder`, `older_than`, `by_sender`, `attachments_by_size`, `top_senders`, `size_per_folder`.
- CLI: `mail-catalog-refresh --mailbox <> [--folder <>]`, `mail-catalog-status`.
- `mail-search --local` uses DuckDB LIKE. Hybrid by default.
- Tests: delta resume after interruption; partial full-sync.
- Bump to 0.9.0.

**Acceptance:**
- First refresh full crawls (inbox + sent + drafts).
- Subsequent refreshes incremental.
- Catalog reflects moves/deletes/categorization on next refresh.
- Delta token expiry (`syncStateNotFound`) triggers clean full-restart with log line.

**Dependencies:** Phase 1. **Parallel-safe:** yes.

---

### Phase 8 — Inbox rules CRUD

**Goal:** Server-side inbox rule management.

**Deliverables:**
- `m365ctl.mail.rules`: list, get, create_from_yaml, update_from_yaml, delete, enable, disable, reorder, export, import.
- `m365ctl.mail.mutate.rules`: wrapped with audit/undo.
- YAML ↔ folderId translator via `m365ctl.mail.folders.resolve_folder_path`.
- CLI: `mail-rules {list,show,create,update,delete,enable,disable,reorder,export,import}`.
- Plan-file support.
- Tests: round-trip export → import → identical set.
- Bump to 0.10.0.

**Dependencies:** Phase 2 (folder resolution). **Parallel-safe:** yes.

---

### Phase 9 — Mailbox settings (OOO, signature, working hours, timezone)

**Goal:** Configure the mailbox.

**Deliverables:**
- `m365ctl.mail.settings`: get/update, auto-reply get/set, signature get/set (with beta-endpoint caveat documented).
- Fallback: signature stored locally at `[mail].signature_path`; sync-to-Outlook documented as best-effort.
- CLI: `mail-settings show`, `mail-ooo {on,off}`, `mail-signature {show,set}`, `mail-settings timezone`, `mail-settings working-hours`.
- OOO duration > 60 days → TTY confirm.
- Tests: round-trip OOO, `m365ctl undo` restores prior.
- Bump to 0.11.0.

**Dependencies:** Phase 1. **Parallel-safe:** yes.

---

### Phase 10 — Triage DSL + engine

**Goal:** YAML rules → plan → confirm pipeline.

**Deliverables:**
- `m365ctl.mail.triage.dsl`: YAML parser → typed Match/Action AST.
- `m365ctl.mail.triage.match`: predicate evaluator with KQL pushdown.
- `m365ctl.mail.triage.plan`: emit plan tagged with `rule_name`.
- CLI: `mail-triage {run,validate}`.
- Examples (generic): `scripts/mail/rules/triage.example.yaml`, `archive-newsletters.yaml`, `daily-triage.yaml`. All use `example.com` domains.
- Tests: DSL round-trip, match evaluator, end-to-end dry-run.
- Bump to 0.12.0.

**Dependencies:** Phase 3 + 4 + 7. **Parallel-safe:** yes.

---

### Phase 11 — Export (EML, MBOX, attachments)

**Goal:** Local backups + compliance exports.

**Deliverables:**
- `m365ctl.mail.export`: `export_message_to_eml`, `export_folder_to_mbox` (streaming), `export_mailbox` (manifest), `export_attachments`.
- CLI: `mail-export {message,folder,mailbox,attachments}`.
- Resume-on-interrupt via manifest progress tracking.
- Tests: round-trip EML → parse → re-export identical; mbox openable in Thunderbird.
- Bump to 0.13.0.

**Dependencies:** Phase 1. **Parallel-safe:** yes.

---

### Phase 12 — Multi-mailbox & delegation

**Goal:** Shared mailboxes; delegation management.

**Deliverables:**
- `m365ctl.mail.endpoints.user_base` handles `shared:<upn>`.
- App-only targeting of `/users/{upn}/messages` gated by `allow_mailboxes`.
- `m365ctl.mail.mutate.delegate` via PnP.PowerShell (`scripts/ps/Set-MailboxDelegate.ps1` — generic).
- CLI: `mail-delegate {list,grant,revoke}`.
- `--mailbox shared:…` works across all commands.
- Tests: live smoke against a dedicated test shared mailbox.
- Bump to 0.14.0.

**Dependencies:** Phase 1. **Parallel-safe:** yes.

---

### Phase 13 — Send-as / on-behalf-of

**Goal:** Send as another mailbox.

**Deliverables:**
- `m365ctl.mail.compose.send_as(from_upn, …)` — app-only `/users/{from_upn}/sendMail`.
- CLI: `mail-sendas <upn> …`.
- Audit log records both `effective_sender` and `authenticated_principal`.
- Mandatory `--unsafe-scope` if `from_upn` not in `allow_mailboxes`.
- Bump to 0.15.0.

**Dependencies:** Phase 5a + Phase 12. **Parallel-safe:** yes.

---

### Phase 14 — Convenience commands

**Goal:** Daily-driver composition on top of the core surface.

**Deliverables:**
- `mail-digest [--since 24h] [--send-to me]` — structured unread summary, optional self-mail.
- `mail-archive --older-than 90d --folder Inbox` — canned archive-to-`Archive/<YYYY>/<MM>` pattern.
- `mail-unsubscribe <message-id>` — detects `List-Unsubscribe`, offers mailto or HTTP.
- `mail-snooze <message-id> --until <iso>` — move to `Deferred/<date>` + dated category; `mail-snooze --process` moves due messages back.
- `mail-top-senders --since 30d --limit 20` — catalog shortcut.
- `mail-size-report` — folder sizes, attachment totals.
- All documented with generic example output in `docs/`.
- Bump to 1.0.0 — this is the "complete" milestone.

**Dependencies:** Phase 7 (catalog) + Phase 10 (DSL for archive patterns). **Parallel-safe:** yes.

---

## 20. Open questions

1. **Signature API.** Graph's roaming-signatures endpoint is still evolving. Phase 9 ships with documented caveats. If Microsoft stabilizes a GA endpoint before Phase 9 lands, adopt it; else fallback works.
2. **Hard-delete EML retention.** Default 30 days in `[logging].retention_days`. Is this right for an open-source default, or too short? Recommendation: 30 days is the published Graph recycle-bin retention; matching it is defensible.
3. **Snooze implementation.** `Deferred/` folder pattern is an opinionated choice. Acceptable as convenience (Phase 14), not core.
4. **MCP server.** Parent spec's Phase 2. Not covered here; would be its own spec after Phase 14 lands and commands are stable for 1+ month of use.
5. **PyPI publication.** Phase 0 doesn't auto-publish. Decide at 1.0.0 (end of Phase 14) whether to push to PyPI or keep install-from-source. No downside to waiting.
6. **Schema for `docs/setup/azure-app-registration.md`.** Should it include screenshots, or text-only with Microsoft Learn links? Recommendation: text + Microsoft Learn links; screenshots go stale quickly.

---

## 21. Out of scope / future work

- **Calendar module** (`m365ctl.calendar`) — events CRUD, free/busy, RSVPs, recurring. Sibling package. Its own spec.
- **Contacts module** (`m365ctl.contacts`) — address book CRUD.
- **Teams module** — chat, channels. Different auth scopes.
- **MCP server** — deferred per parent spec.
- **ML triage classifier** — distinct feature alongside DSL.
- **Quick Steps equivalent** — parameterized multi-action macros on DSL.
- **Webhook subscriptions** — `/subscriptions` needs reachable endpoint; out of scope for CLI.
- **Full-text mail body local index** — revisit after catalog usage shows need.

---

## 22. Execution notes for future sessions

When implementing a phase in a fresh session:

1. **Read this spec first.** Then read the renamed parent spec (`2026-04-24-m365ctl-design.md`) — safety model + conventions are defined there.
2. **Author a plan file** in `docs/superpowers/plans/<date>-<phase-name>.md` mirroring `2026-04-24-foundation-and-auth.md` style — task-by-task checkboxes, files touched, expected output per step.
3. **Invoke `superpowers:writing-plans`** to draft; then **`superpowers:executing-plans`** (or `subagent-driven-development` for parallel tasks) to implement.
4. **TDD by default.** Each mutation lands with unit + mocked integration + live smoke gated by `M365CTL_LIVE_TESTS=1`.
5. **Op-log schema stability.** Additional `before`/`after` fields must be additive. Undo dispatcher tolerates missing optional fields.
6. **End every phase with `m365ctl whoami` green** — scopes consented, catalog fresh, no orphaned test data in the live mailbox.
7. **Run the full `od-*` test suite** after each phase to ensure Phase 0's guarantees haven't regressed.
8. **Run the §4.5 grep suite** after each phase. Zero Fazla references. Zero tenant-specific leaks.
9. **Bump version + CHANGELOG** at the end of each phase.
