# Fazla OneDrive Toolkit — Design

- **Status:** approved (brainstorming)
- **Date:** 2026-04-24
- **Owner:** ae (ardaeren13@gmail.com)
- **Tenant:** Fazla (Microsoft 365, admin access held)
- **Next step:** implementation plan via `superpowers:writing-plans`

## 1. Goal

Give Claude Code CLI-driven control of the Fazla Microsoft 365 tenant's OneDrive + SharePoint content (~2TB, tenant-wide) so that search, inventory, reorganization, cleanup, classification, content extraction, and sharing audits can be performed from a terminal without the M365 web UI.

## 2. Scope

### In scope
- All seven operation categories: (a) search, (b) inventory & reporting, (c) reorganize, (d) cleanup, (e) classify/tag, (f) content extraction, (g) sharing/permissions audit.
- Tenant-wide access via a dedicated Azure AD application.
- Hybrid execution model: Microsoft Graph API as primary surface; `rclone` bisync for targeted local workspaces.
- Local DuckDB catalog as a refreshable cache over tenant metadata.

### Out of scope (phase 1)
- MCP server wrapping the CLI. Deferred to phase 2 once scripts are stable.
- A web UI of any kind.
- Teams / Exchange / Entra ID object management beyond what Graph needs for OneDrive/SharePoint.
- Automated retention policy design; this toolkit *applies* existing labels, it does not *define* the label taxonomy.

## 3. Authentication

### Azure AD application
- Single-tenant app in Fazla tenant: "Fazla OneDrive Toolkit".
- Two auth flows backed by the same app registration:
  - **Delegated** (device-code) — for operations attributed to the signed-in user.
  - **App-only** (certificate, `client_credentials`) — for tenant-wide reads and unattended jobs.

### Credentials
- **Primary auth: certificate.** Self-signed X.509, RSA-4096, SHA-256, 2-year validity.
  - Subject: `CN=FazlaODToolkit`
  - Private key: `~/.config/fazla-od/fazla-od.key` (mode 600, macOS FileVault at rest, never in git).
  - Public cert: `~/.config/fazla-od/fazla-od.cer` uploaded to Entra.
  - SHA-1 thumbprint: `C38CC9B49D5E4D326B4A79ECAF33CD65B008BCBF`
  - Expires: 2028-04-22. `od-auth whoami` surfaces days-until-expiry on every run.
- **No client secrets.** The prior secret was rotated-and-deleted after the cert was uploaded.

### Graph permissions requested
- **Delegated:** `Files.ReadWrite.All`, `Sites.ReadWrite.All`, `User.Read`.
- **Application:** `Files.ReadWrite.All`, `Sites.ReadWrite.All`, `User.Read.All`, `Directory.Read.All`, `InformationProtectionPolicy.Read.All`.
- Admin consent granted at tenant level.

### Token caching
- Delegated tokens cached via MSAL in macOS Keychain item `FazlaODToolkit:DelegatedTokenCache`.
- App-only tokens are short-lived and not persisted; minted fresh per run via cert.

## 4. Configuration

Single `config.toml` at repo root (gitignored; a `config.toml.example` is tracked).

```toml
tenant_id    = "…"                           # Directory (tenant) ID
client_id    = "…"                           # Application (client) ID
cert_path    = "~/.config/fazla-od/fazla-od.key"   # PEM, private key
cert_public  = "~/.config/fazla-od/fazla-od.cer"   # PEM, public cert
default_auth = "delegated"                   # app-only must be opt-in per command

[scope]
allow_drives         = ["me", "site:Finance", "site:Legal"]
allow_users          = ["*"]
deny_paths           = ["/Confidential/**", "/HR/**"]
unsafe_requires_flag = true

[catalog]
path             = "cache/catalog.duckdb"
refresh_on_start = false

[logging]
ops_dir = "logs/ops"
```

Scope allow-list is enforced by every mutating command. Anything outside requires `--unsafe-scope` AND an interactive `/dev/tty` `y/N` confirmation that Claude cannot auto-answer.

## 5. Architecture

### Polyglot tool layer

Three families, each chosen for what it does best:

**`rclone` — bulk file I/O.** Two pre-configured remotes in `rclone/rclone.conf`:
- `od-me:` — user's OneDrive, delegated auth.
- `sp:` — SharePoint root, app-only cert auth.
Handles list, copy, move, sync, bisync, recycle-bin delete. Does not handle labels, permissions, metadata columns, or search.

**PowerShell (`scripts/ps/*.ps1`)** — `PnP.PowerShell` + `Microsoft.Graph` modules via `pwsh` on macOS. Owns operations where Microsoft-native cmdlets dominate: permissions/sharing audit (g), sensitivity + retention labels (e), tenant-wide site enumeration, stale share cleanup (d).

**Python (`scripts/py/*.py`)** — `msgraph-sdk` + `msal` + `duckdb`. Owns custom search (a), cross-drive inventory crawls (b), dedup detection, report generation, catalog refresh, JSON munging.

### Local catalog

- **Engine:** DuckDB (`cache/catalog.duckdb`).
- **Rationale:** 2TB tenant-wide listing over the Graph wire is infeasible per query. A local flat index enables fast analytical queries without API round-trips.
- **Schema (core table `items`):**
  `drive_id`, `item_id`, `parent_path`, `name`, `size`, `mime_type`, `created_at`, `modified_at`, `owner`, `has_children`, `has_sharing`, `etag`, `quick_xor_hash`, `last_seen`.
- **Refresh mechanism:** `bin/od-catalog-refresh --scope me|tenant|site:<id>` uses Graph `delta` endpoints. First call is a full crawl; subsequent calls pull only changes via the persisted `deltaLink` per drive.
- **Consistency model:** catalog is a *cache*; Graph is truth. All mutating commands re-verify each target item against Graph before acting. A stale catalog never causes wrong deletes — only occasional "item not found, skipping" log lines.

### Data flow example — `od-search "invoice" --scope tenant`

1. Python script resolves scope → list of drive IDs.
2. Parallel: (a) Graph `/search/query` for server-side full-text match over indexed content; (b) DuckDB SELECT over `items` for filename/path match.
3. Results merged, deduped by `item_id`, ordered by relevance + modified_at.
4. Emitted as JSON to stdout.

## 6. CLI surface (`bin/*`)

Each command is a thin POSIX shell wrapper dispatching to Python or PowerShell. All commands accept a shared flag vocabulary.

| Command | Purpose | Op category |
|---|---|---|
| `od-auth login` | Device-code delegated login; caches token in Keychain | — |
| `od-auth whoami` | Identity, scopes, cert expiry, catalog staleness | — |
| `od-catalog-refresh` | Delta-crawl scope into DuckDB catalog | — |
| `od-search <query>` | Metadata + full-text search | (a) |
| `od-inventory` | Catalog queries: `--top-by-size`, `--stale-since`, `--by-owner`, `--duplicates`, `--sql` | (b) |
| `od-move`, `od-rename`, `od-copy` | Bulk mutations; plan-file workflow | (c) |
| `od-clean` | Recycle bin, old versions, stale shares | (d) |
| `od-label` | Apply/remove sensitivity + retention labels | (e) |
| `od-download` | Materialize a subset to local path | (f) |
| `od-audit-sharing` | Permissions & sharing report | (g) |
| `od-sync-workspace` | Manage targeted `workspaces/<name>/` bisync folders | hybrid |
| `od-undo <op_id>` | Replay a reverse-op from the audit log, where reversible | — |

### Shared flags
- `--scope me|tenant|site:<id>|drive:<id>` (explicit, never implicit)
- `--json` (default output when invoked by Claude)
- `--dry-run` (default for all mutating commands)
- `--confirm` (required to execute)
- `--plan-out <path>`, `--from-plan <path>` (plan-file workflow)
- `--unsafe-scope` (bypass allow-list; requires TTY confirm)
- `--limit`, `--page-size`

## 7. Safety model

At 2TB with tenant-wide app-only permissions, safety is load-bearing. Non-negotiable rules:

1. **Dry-run is the default** for every mutating command. `--confirm` is required to execute.
2. **Bulk destructive ops require a plan file.** "Bulk" = any mutation whose target set is defined by a pattern, query, or catalog selection rather than one explicit `item_id`.
   - `od-move --pattern "**/*.tmp" --plan-out plan.json` → writes JSON list of exact item IDs and intended actions.
   - Review step.
   - `od-move --from-plan plan.json --confirm` → executes exactly that plan, no glob re-expansion.
   - Wildcards never go straight to mutation. Single-item mutations (`od-rename <item_id> <new_name> --confirm`) skip the plan-file requirement but still log to the audit trail.
3. **Scope allow-list enforced in every command.** Anything outside requires `--unsafe-scope` + interactive `/dev/tty` confirm.
4. **Deny paths are absolute.** Items matching `deny_paths` are filtered out before plan generation and never appear in dry-run output.
5. **Every mutation writes to `logs/ops/YYYY-MM-DD.jsonl`**: `{ts, op_id, cmd, args, item_id, before, after, result}`.
6. **No hard deletes.** Deletes route to OneDrive recycle bin. `od-clean recycle-bin` is a separate, explicit command.
7. **Rate-limit aware.** All scripts share a retry helper: exponential backoff with `Retry-After` header honored for 429/503.
8. **`od-undo <op_id>`** replays reverse-ops from the audit log where reversible (label changes, moves, renames). Recycle-bin deletes and permission revocations are flagged non-reversible; undo emits instructions for manual restore.

## 8. Claude integration

### Phase 1 — Bash
- `AGENTS.md` at repo root documents: CLI surface, safety model, required-flag order (`--dry-run` → `--plan-out` → review → `--from-plan --confirm`), scope allow-list. Claude reads this at session start.
- Repo `.claude/settings.json` allowlists read-only `od-*` subcommands (via `fewer-permission-prompts` skill) while keeping mutating subcommands subject to approval.
- Typical Claude workflow for a user request:
  1. `od-search` / `od-inventory` to gather candidates.
  2. Generate plan file via `--plan-out`.
  3. Show plan to user; wait for explicit approval.
  4. Execute `--from-plan --confirm`.
  5. Summarize from `logs/ops/` entries.

### Phase 2 — MCP (deferred)
Wrap stable commands as an MCP server with typed tools:
- Read-only: `onedrive_search`, `onedrive_inventory`, `onedrive_audit_sharing`, `onedrive_download`.
- Mutating: `onedrive_move`, `onedrive_rename`, `onedrive_copy`, `onedrive_label`, `onedrive_clean` — each with `dry_run: bool = true` and `confirm: bool = false` as typed defaults, enforcing safety structurally.

## 9. Directory layout

```
Fazla-OneDrive/
├── AGENTS.md
├── README.md
├── config.toml.example            # tracked
├── .gitignore                     # excludes config.toml, cache/, workspaces/, logs/
├── bin/                           # POSIX shell wrappers
├── scripts/
│   ├── ps/                        # PowerShell admin scripts
│   └── py/                        # Python Graph scripts
├── rclone/
│   └── rclone.conf.example        # tracked; real rclone.conf gitignored
├── cache/                         # gitignored — catalog.duckdb
├── workspaces/                    # gitignored — rclone bisync targets
├── logs/ops/                      # gitignored — jsonl audit log
└── docs/superpowers/specs/        # this document
```

Credentials (`~/.config/fazla-od/*`) live **outside** the repo and are never referenced by relative path.

## 10. Open items before implementation

The following must be resolved in the implementation-plan session:

1. **Label which UUID is which.** User provided two UUIDs (`361efb70-…` and `b22e6fd3-…`); exact assignment to `tenant_id` vs `client_id` must be confirmed against the Entra app Overview page before either is written to `config.toml`.
2. **Confirm permission sets added.** User confirmed "admin consent granted"; the implementation plan must verify both Delegated and Application sets are present (not only one) before running any app-only flow.
3. **Pick the first "workspace" slice** to enable rclone bisync against (e.g. the user's own OneDrive root, or a specific site library), so `od-sync-workspace` has a smoke-test target.
4. **Initial `scope.allow_drives` list.** Currently illustrative; must be replaced with real site slugs before the first tenant-wide run.
5. **Git repo initialization.** Project directory is not yet a git repo; implementation plan should `git init` and scaffold `.gitignore` before any other files are written, so credentials can never be accidentally staged.

## 11. Success criteria

The toolkit is considered complete when:

- `od-auth whoami` returns identity, scopes, and cert expiry for both delegated and app-only flows without user interaction beyond the one-time device-code login.
- `od-catalog-refresh --scope tenant` completes a full initial crawl and subsequent delta refreshes in < 10 minutes on steady-state.
- Each of the seven operation categories has at least one working command demonstrated end-to-end on a non-production scope (e.g. the user's own OneDrive).
- All mutating commands enforce dry-run default and plan-file workflow; a deliberate attempt to bypass is rejected with a clear error.
- `AGENTS.md` enables a fresh Claude session to execute a realistic multi-step request (e.g. "find all PDFs over 100MB not touched since 2023 in the Finance site, show me a plan, then move them to an Archive folder") without additional guidance.
