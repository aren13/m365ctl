# m365ctl

[![PyPI](https://img.shields.io/pypi/v/m365ctl.svg)](https://pypi.org/project/m365ctl/)
[![CI](https://github.com/aren13/m365ctl/actions/workflows/ci.yml/badge.svg)](https://github.com/aren13/m365ctl/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

An admin CLI for Microsoft 365 — OneDrive, SharePoint, and Exchange Mail — built on Microsoft Graph.

Safe by default: dry-run, plan-file workflow, scope allow-lists, audit log, and undo on every mutation.

## Table of contents

- [Highlights](#highlights)
- [Installation](#installation)
- [Quickstart](#quickstart)
- [Features](#features)
- [Safety model](#safety-model)
- [Configuration](#configuration)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)
- [Disclaimer](#disclaimer)

## Highlights

- **Read-only by default.** Mutating verbs require `--confirm` and are logged.
- **Local catalog.** DuckDB mirrors of files and mail enable offline queries.
- **Plan / replay.** Bulk operations produce reviewable plan files before execution.
- **Undoable.** Every mutation records before/after state for `m365ctl undo`.
- **Scope guards.** `allow_drives` / `allow_mailboxes` / `deny_paths` enforced on every call.
- **App-only auth.** Certificate-based auth against your Entra app registration.

## Installation

Requires Python 3.11+. Install with [`uv`](https://docs.astral.sh/uv/):

```bash
# Standalone CLI (recommended for end users):
uv tool install m365ctl

# Or as a project dependency:
uv add m365ctl

# Or with pipx / pip:
pipx install m365ctl
```

This installs the `m365ctl` console script. Verify with `m365ctl --help`.

For local development from source:

```bash
git clone https://github.com/aren13/m365ctl
cd m365ctl
uv sync --all-extras
# The repo's bin/*.sh shims (./bin/od-auth, ./bin/mail-list, ...) are
# convenience aliases for `uv run m365ctl <domain> <verb>`.
```

## Quickstart

1. **Register an Entra app** and grant Graph permissions — see [docs/setup/azure-app-registration.md](docs/setup/azure-app-registration.md).
2. **Generate a client certificate** — see [docs/setup/certificate-auth.md](docs/setup/certificate-auth.md).
3. **Configure** the CLI:

   ```bash
   # Grab the template from the repo or copy from `m365ctl --help` output.
   curl -O https://raw.githubusercontent.com/aren13/m365ctl/main/config.toml.example
   mv config.toml.example config.toml
   # Edit config.toml: tenant id, app id, cert path, allow-lists.
   ```

4. **Authenticate and verify**:

   ```bash
   m365ctl od auth login
   m365ctl od auth whoami
   m365ctl mail whoami
   ```

Full walkthrough: [docs/setup/first-run.md](docs/setup/first-run.md) (≤ 20 minutes from install to whoami).

## Features

### OneDrive and SharePoint

| Capability        | Commands                                       |
| ----------------- | ---------------------------------------------- |
| Catalog mirror    | `od-catalog-refresh`, `od-catalog-status`      |
| Search            | `od-search` (server and local)                 |
| Inventory         | `od-inventory` (top-by-size, top-by-age, dups) |
| File operations   | `od-move`, `od-rename`, `od-copy`              |
| Sensitivity       | `od-label`                                     |
| Sharing audit     | `od-audit-sharing`                             |
| Recycle bin       | `od-clean`, `od-delete`                        |
| Download          | `od-download` (bulk + resumable)               |

### Mail

| Capability         | Commands                                                       |
| ------------------ | -------------------------------------------------------------- |
| Read               | `mail-list`, `mail-get`, `mail-read`, `mail-search`            |
| Folders / labels   | `mail-folders`, `mail-categories`, `mail-categorize`           |
| Compose            | `mail-draft`, `mail-send`, `mail-reply`, `mail-forward`        |
| Move / flag        | `mail-move`, `mail-copy`, `mail-flag`, `mail-focus`            |
| Inbox rules        | `mail-rules` (create, update, delete, import, export)          |
| Mailbox settings   | `mail-settings`, `mail-ooo`, `mail-signature`                  |
| Triage DSL         | `mail-triage` (YAML rules over local catalog)                  |
| Catalog            | `mail-catalog-refresh`, `mail-catalog-status`                  |
| Export             | `mail-export` (EML, MBOX, attachments, full mailbox)           |
| Hard delete        | `mail-clean`, `mail-empty` (triple-gated)                      |
| Delegation         | `mail-delegate`, `--mailbox shared:<addr>` routing             |
| Send-as            | `mail-sendas` (app-only, fully audited)                        |
| Scheduled send     | `mail-send --schedule-at <iso>`                                |
| Convenience verbs  | `mail-digest`, `mail-archive`, `mail-snooze`, `mail-top-senders`, `mail-size-report`, `mail-unsubscribe` |

Each verb supports `--help`. See [docs/mail/convenience-commands.md](docs/mail/convenience-commands.md) for the convenience-verb reference.

## Safety model

| Layer            | Behavior                                                                                              |
| ---------------- | ----------------------------------------------------------------------------------------------------- |
| Dry-run default  | Mutating verbs print a plan and exit unless `--confirm` is passed.                                    |
| Plan files       | Bulk operations write a plan file that can be reviewed before `--replay`.                             |
| Scope allow-lists| `allow_drives` / `allow_mailboxes` gate every API call; `deny_paths` / `deny_folders` are absolute.   |
| Audit log        | Each mutation records a before/after block to a tamper-resistant log.                                 |
| Undo             | `m365ctl undo <op-id>` (or `od-undo` / `mail-undo`) replays the inverse using the audit record.       |
| Hard-delete gate | Irreversible deletes require `--confirm`, a TTY-typed phrase, and an extra escalation for large ops.  |

## Configuration

`config.toml` controls auth, scopes, paths, and feature flags. Start from `config.toml.example`:

```toml
[auth]
tenant_id   = "..."
client_id   = "..."
cert_path   = "~/.m365ctl/cert.pem"

[scope]
allow_drives    = ["..."]
allow_mailboxes = ["user@example.com"]
deny_paths      = ["/Confidential"]

[mail]
schedule_send_enabled = false
```

Catalog files, plan files, and the audit log default to `cache/`, `plans/`, and `logs/` under the project root — override per `config.toml`.

## Documentation

| Topic                          | Link                                                                                |
| ------------------------------ | ----------------------------------------------------------------------------------- |
| Azure app registration         | [docs/setup/azure-app-registration.md](docs/setup/azure-app-registration.md)        |
| Certificate-based auth         | [docs/setup/certificate-auth.md](docs/setup/certificate-auth.md)                    |
| First-run walkthrough          | [docs/setup/first-run.md](docs/setup/first-run.md)                                  |
| PnP PowerShell prerequisites   | [docs/ops/pnp-powershell-setup.md](docs/ops/pnp-powershell-setup.md)                |
| Mail convenience verbs         | [docs/mail/convenience-commands.md](docs/mail/convenience-commands.md)              |
| Roadmap                        | [docs/roadmap.md](docs/roadmap.md)                                                  |
| Changelog                      | [CHANGELOG.md](CHANGELOG.md)                                                        |
| Contributing                   | [CONTRIBUTING.md](CONTRIBUTING.md)                                                  |

## Contributing

Issues and pull requests welcome. Before opening a PR, please read [CONTRIBUTING.md](CONTRIBUTING.md) and run the test suite:

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

## License

Apache-2.0. See [LICENSE](LICENSE).

## Disclaimer

This is an independent open-source project. Not affiliated with, endorsed by, or supported by Microsoft. "Microsoft 365", "OneDrive", "SharePoint", and "Exchange" are trademarks of Microsoft Corporation.
