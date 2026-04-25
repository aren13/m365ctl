# Phase 12 — Multi-Mailbox & Delegation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan group-by-group. Steps use checkbox (`- [ ]`) syntax.

**Goal:** First-class shared-mailbox support across every shipped verb + delegation management via PnP.PowerShell.

**Architecture:**
- `m365ctl.mail.endpoints.user_base` already routes `shared:<addr>` to `/users/<addr>` (verified). Phase 12 adds **integration coverage** to confirm every shipped verb honors `--mailbox shared:…` end-to-end. No re-routing work needed at the endpoint layer.
- Consolidate the three duplicated `_derive_mailbox_upn` helpers (catalog.py / export.py / triage.py) into one shared `mail.cli._common.derive_mailbox_upn` so future verbs use the canonical version.
- New `m365ctl.mail.mutate.delegate` Python module wrapping a PnP.PowerShell script (`scripts/ps/Set-MailboxDelegate.ps1`). Three operations: list, grant, revoke. PnP-driven (Exchange Online cmdlets) because Graph Microsoft 365 doesn't expose mailbox-permission CRUD.
- New `m365ctl.mail.cli.delegate` with subcommands `mail delegate {list, grant, revoke}`. Bin wrapper `bin/mail-delegate`.
- New PS script `scripts/ps/Set-MailboxDelegate.ps1` — uses `Connect-ExchangeOnline` + `Get-MailboxPermission` / `Add-MailboxPermission` / `Remove-MailboxPermission`. Generic (no tenant-specific config).

**Tech stack:** Existing `m365ctl.onedrive.mutate._pwsh.invoke_pwsh` shell-out helper. Existing safety/audit/undo plumbing. No new Python deps.

**Baseline:** `main` post-PR-#18 (946718b), 821 passing tests, 0 mypy errors. Tag `v1.1.0` shipped.

**Version bump:** 1.1.0 → 1.2.0.

---

## File Structure

**New:**
- `scripts/ps/Set-MailboxDelegate.ps1` — Connect-ExchangeOnline + Get/Add/Remove-MailboxPermission.
- `src/m365ctl/mail/mutate/delegate.py` — `DelegateResult`, `list_delegates`, `execute_grant`, `execute_revoke`.
- `src/m365ctl/mail/cli/delegate.py` — argparse for `mail delegate {list, grant, revoke}`.
- `bin/mail-delegate` — exec wrapper.
- `tests/test_mail_endpoints_shared.py` — integration tests asserting `shared:` flows correctly through `list_messages`, `list_folders`, `get_settings`, `update_mailbox_settings`, etc.
- `tests/test_mail_mutate_delegate.py` — executor tests with mocked `invoke_pwsh`.
- `tests/test_cli_mail_delegate.py` — CLI tests.

**Modify:**
- `src/m365ctl/mail/cli/_common.py` — add canonical `derive_mailbox_upn(spec) -> str`.
- `src/m365ctl/mail/cli/{catalog,export,triage}.py` — replace local `_derive_mailbox_upn` calls with the imported canonical helper.
- `src/m365ctl/mail/cli/__main__.py` — route new `delegate` verb + `_USAGE` line.
- `pyproject.toml` — bump 1.1.0 → 1.2.0.
- `CHANGELOG.md` — 1.2.0 section.
- `README.md` — Mail bullet.

---

## Group 1 — `shared:` audit + helper consolidation

**Files:**
- Modify: `src/m365ctl/mail/cli/_common.py`
- Modify: `src/m365ctl/mail/cli/catalog.py`, `cli/export.py`, `cli/triage.py`
- Create: `tests/test_mail_endpoints_shared.py`

### Task 1.1: Promote `_derive_mailbox_upn` to `mail.cli._common.derive_mailbox_upn` (one commit)

Currently three CLI modules define `_derive_mailbox_upn` with the same body:
```python
def _derive_mailbox_upn(spec: str) -> str:
    if spec == "me":
        return "me"
    if spec.startswith("upn:") or spec.startswith("shared:"):
        return spec.split(":", 1)[1]
    return spec
```

That maps a spec like `shared:team@example.com` → `team@example.com` for use as a catalog mailbox key.

- [ ] **Step 1:** Add `derive_mailbox_upn(spec: str) -> str` to `src/m365ctl/mail/cli/_common.py` (public symbol, no leading underscore). Same body.

- [ ] **Step 2:** Replace each call site:
  - `src/m365ctl/mail/cli/catalog.py` — drop the local `_derive_mailbox_upn`, `from m365ctl.mail.cli._common import derive_mailbox_upn`, call sites use the imported name.
  - `src/m365ctl/mail/cli/export.py` — same.
  - `src/m365ctl/mail/cli/triage.py` — same.

- [ ] **Step 3:** Add a test (`tests/test_mail_cli_common.py` — or extend an existing test file) covering the canonical helper:
  - `derive_mailbox_upn("me") == "me"`
  - `derive_mailbox_upn("upn:alice@example.com") == "alice@example.com"`
  - `derive_mailbox_upn("shared:team@example.com") == "team@example.com"`
  - `derive_mailbox_upn("alice@example.com") == "alice@example.com"` (passthrough)

- [ ] **Step 4:** Quality gates. Existing tests for catalog/export/triage CLIs should still pass — they use the same logic via the imported name.

- [ ] **Step 5:** Commit:
```
git add src/m365ctl/mail/cli/_common.py \
        src/m365ctl/mail/cli/catalog.py \
        src/m365ctl/mail/cli/export.py \
        src/m365ctl/mail/cli/triage.py \
        tests/test_mail_cli_common.py
git commit -m "refactor(mail/cli): promote _derive_mailbox_upn to cli._common.derive_mailbox_upn (DRY)"
```

### Task 1.2: Integration tests for `shared:` end-to-end (one commit)

The `user_base` helper already routes `shared:<addr>` → `/users/<addr>`. We'll lock that in with explicit tests at the verb layer, so future regressions in any reader/mutator surface immediately.

- [ ] **Step 1:** Tests at `tests/test_mail_endpoints_shared.py`:
  - `list_messages(graph, mailbox_spec="shared:team@example.com", auth_mode="app-only", folder_id="inbox", parent_folder_path="Inbox")` → first `graph.get_paginated` call uses path `/users/team@example.com/mailFolders/inbox/messages`.
  - `list_folders(graph, mailbox_spec="shared:team@example.com", auth_mode="app-only")` → first call to `/users/team@example.com/mailFolders`.
  - `get_settings(graph, mailbox_spec="shared:team@example.com", auth_mode="app-only")` → call to `/users/team@example.com/mailboxSettings`.
  - `update_mailbox_settings(graph, mailbox_spec="shared:team@example.com", auth_mode="app-only", body={...})` → PATCH `/users/team@example.com/mailboxSettings`.
  - `resolve_folder_path("inbox", graph, mailbox_spec="shared:team@example.com", auth_mode="app-only")` → calls `/users/team@example.com/mailFolders/inbox`.
  - `derive_mailbox_upn("shared:team@example.com")` → `"team@example.com"`.

  All use `MagicMock` graph; assertions on the URL paths only. No live calls.

- [ ] **Step 2:** Audit `assert_mailbox_allowed` honors `shared:`:
  - Add a test verifying `allow_mailboxes=["shared:team@example.com"]` permits a `shared:team@example.com` spec and rejects `shared:other@example.com`.
  - Add a test verifying `allow_mailboxes=["upn:team@example.com"]` does NOT auto-permit `shared:team@example.com` (the spec strings must match exactly, per `safety.py:222`).

- [ ] **Step 3:** Commit:
```
test(mail): integration coverage for `shared:<addr>` across readers + mutators
```

---

## Group 2 — Delegation: PnP.PowerShell script + Python wrapper

**Files:**
- Create: `scripts/ps/Set-MailboxDelegate.ps1`
- Create: `src/m365ctl/mail/mutate/delegate.py`
- Create: `tests/test_mail_mutate_delegate.py`

### Task 2.1: PowerShell script

Generic (no tenant-specific defaults). Takes `--Mailbox`, `--Action <List|Grant|Revoke>`, `--Delegate <upn>`, `--AccessRights <FullAccess|SendAs|SendOnBehalf>` parameters and emits machine-parseable output (one JSON object per line).

- [ ] **Step 1:** Create `scripts/ps/Set-MailboxDelegate.ps1`:

```powershell
<#
.SYNOPSIS
  Manage mailbox delegation via Exchange Online PowerShell.

.DESCRIPTION
  Wrapper used by m365ctl mail-delegate. Outputs JSONL on stdout so the
  Python caller can parse cleanly.

.PARAMETER Mailbox
  Target mailbox UPN (the mailbox being delegated).

.PARAMETER Action
  One of: List, Grant, Revoke.

.PARAMETER Delegate
  Delegate UPN (required for Grant and Revoke).

.PARAMETER AccessRights
  Permission level. One of: FullAccess, SendAs, SendOnBehalf.
  Defaults to FullAccess.

.EXAMPLE
  pwsh -NoProfile -File Set-MailboxDelegate.ps1 -Mailbox team@example.com -Action List
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$Mailbox,
  [Parameter(Mandatory=$true)][ValidateSet('List','Grant','Revoke')][string]$Action,
  [string]$Delegate,
  [ValidateSet('FullAccess','SendAs','SendOnBehalf')][string]$AccessRights = 'FullAccess'
)

$ErrorActionPreference = 'Stop'

# Connect-ExchangeOnline silently if not already connected.
if (-not (Get-Command Connect-ExchangeOnline -ErrorAction SilentlyContinue)) {
  Write-Error "ExchangeOnlineManagement module not installed. Install-Module ExchangeOnlineManagement -Scope CurrentUser"
  exit 2
}
try {
  Get-PSSession -ErrorAction SilentlyContinue | Where-Object { $_.ConfigurationName -eq 'Microsoft.Exchange' -and $_.State -eq 'Opened' } | Out-Null
  if (-not $?) { Connect-ExchangeOnline -ShowBanner:$false -ErrorAction Stop }
} catch {
  Connect-ExchangeOnline -ShowBanner:$false -ErrorAction Stop
}

function Emit-Json([object]$obj) {
  $obj | ConvertTo-Json -Compress -Depth 5
}

switch ($Action) {
  'List' {
    $perms = Get-MailboxPermission -Identity $Mailbox |
      Where-Object { -not $_.IsInherited -and $_.User -notmatch '^NT AUTHORITY\\' }
    foreach ($p in $perms) {
      Emit-Json @{
        kind          = 'FullAccess'
        mailbox       = $Mailbox
        delegate      = [string]$p.User
        access_rights = $p.AccessRights -join ','
        deny          = $p.Deny
      }
    }
    $sendas = Get-RecipientPermission -Identity $Mailbox -ErrorAction SilentlyContinue |
      Where-Object { $_.AccessRights -contains 'SendAs' }
    foreach ($s in $sendas) {
      Emit-Json @{
        kind          = 'SendAs'
        mailbox       = $Mailbox
        delegate      = [string]$s.Trustee
        access_rights = 'SendAs'
        deny          = $false
      }
    }
    $mbx = Get-Mailbox -Identity $Mailbox
    foreach ($g in @($mbx.GrantSendOnBehalfTo)) {
      if (-not $g) { continue }
      Emit-Json @{
        kind          = 'SendOnBehalf'
        mailbox       = $Mailbox
        delegate      = [string]$g
        access_rights = 'SendOnBehalf'
        deny          = $false
      }
    }
    exit 0
  }
  'Grant' {
    if (-not $Delegate) { Write-Error 'Grant requires -Delegate'; exit 2 }
    switch ($AccessRights) {
      'FullAccess' {
        Add-MailboxPermission -Identity $Mailbox -User $Delegate -AccessRights FullAccess -InheritanceType All -AutoMapping:$false | Out-Null
      }
      'SendAs' {
        Add-RecipientPermission -Identity $Mailbox -Trustee $Delegate -AccessRights SendAs -Confirm:$false | Out-Null
      }
      'SendOnBehalf' {
        Set-Mailbox -Identity $Mailbox -GrantSendOnBehalfTo @{Add=$Delegate} | Out-Null
      }
    }
    Emit-Json @{ status='ok'; action='Grant'; mailbox=$Mailbox; delegate=$Delegate; access_rights=$AccessRights }
    exit 0
  }
  'Revoke' {
    if (-not $Delegate) { Write-Error 'Revoke requires -Delegate'; exit 2 }
    switch ($AccessRights) {
      'FullAccess' {
        Remove-MailboxPermission -Identity $Mailbox -User $Delegate -AccessRights FullAccess -InheritanceType All -Confirm:$false | Out-Null
      }
      'SendAs' {
        Remove-RecipientPermission -Identity $Mailbox -Trustee $Delegate -AccessRights SendAs -Confirm:$false | Out-Null
      }
      'SendOnBehalf' {
        Set-Mailbox -Identity $Mailbox -GrantSendOnBehalfTo @{Remove=$Delegate} | Out-Null
      }
    }
    Emit-Json @{ status='ok'; action='Revoke'; mailbox=$Mailbox; delegate=$Delegate; access_rights=$AccessRights }
    exit 0
  }
}
```

(One commit at the end of Task 2.2 includes the script + Python wrapper together.)

### Task 2.2: Python wrapper module (one commit, includes Task 2.1 PS script)

- [ ] **Step 1: Failing tests** at `tests/test_mail_mutate_delegate.py`:
  - `list_delegates(mailbox)` invokes `pwsh -File Set-MailboxDelegate.ps1 -Mailbox X -Action List`, parses JSONL stdout, returns list of `DelegateEntry(kind, delegate, access_rights, deny)`.
  - `execute_grant(op, ...)` invokes `pwsh ... -Action Grant -Delegate Y -AccessRights Z` and returns `DelegateResult(status="ok")` on `exit 0`.
  - `execute_revoke(op, ...)` invokes `... -Action Revoke -Delegate Y -AccessRights Z` and returns `DelegateResult(status="ok")` on `exit 0`.
  - On non-zero exit: returns `DelegateResult(status="error", error=stderr)`.
  - On `pwsh` not on PATH (`FileNotFoundError`): returns `DelegateResult(status="error")` with a clear message including "ExchangeOnlineManagement" install hint.
  - Audit: each executor calls `log_mutation_start` / `log_mutation_end` matching the existing audit API.

  Patch `m365ctl.onedrive.mutate._pwsh.invoke_pwsh` (since the new module imports it).

- [ ] **Step 2: Implement** `src/m365ctl/mail/mutate/delegate.py`:

```python
"""Mailbox delegation via the Set-MailboxDelegate.ps1 PnP.PowerShell wrapper."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.planfile import Operation
from m365ctl.onedrive.mutate._pwsh import PS_SCRIPTS_DIR, invoke_pwsh


_PS_SCRIPT = PS_SCRIPTS_DIR / "Set-MailboxDelegate.ps1"


AccessRights = Literal["FullAccess", "SendAs", "SendOnBehalf"]


@dataclass(frozen=True)
class DelegateEntry:
    kind: str           # 'FullAccess' | 'SendAs' | 'SendOnBehalf'
    mailbox: str
    delegate: str
    access_rights: str
    deny: bool


@dataclass
class DelegateResult:
    op_id: str
    status: str         # 'ok' | 'error'
    error: str | None = None
    after: dict[str, Any] = field(default_factory=dict)


_PWSH_HINT = (
    "pwsh not on PATH. Install PowerShell 7+ and the ExchangeOnlineManagement "
    "module: `Install-Module ExchangeOnlineManagement -Scope CurrentUser`."
)


def list_delegates(mailbox: str) -> list[DelegateEntry]:
    try:
        rc, out, err = invoke_pwsh(_PS_SCRIPT, ["-Mailbox", mailbox, "-Action", "List"])
    except FileNotFoundError as e:
        raise RuntimeError(_PWSH_HINT) from e
    if rc != 0:
        raise RuntimeError(f"List-Delegates failed (rc={rc}): {err.strip() or out.strip()}")
    out_lines = [line for line in out.splitlines() if line.strip()]
    entries: list[DelegateEntry] = []
    for line in out_lines:
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue  # noise / informational lines
        entries.append(DelegateEntry(
            kind=d.get("kind", ""),
            mailbox=d.get("mailbox", mailbox),
            delegate=d.get("delegate", ""),
            access_rights=d.get("access_rights", ""),
            deny=bool(d.get("deny", False)),
        ))
    return entries


def execute_grant(
    op: Operation, logger: AuditLogger, *, before: dict | None = None,
) -> DelegateResult:
    return _do(op, logger, action="Grant", before=before)


def execute_revoke(
    op: Operation, logger: AuditLogger, *, before: dict | None = None,
) -> DelegateResult:
    return _do(op, logger, action="Revoke", before=before)


def _do(
    op: Operation, logger: AuditLogger, *, action: str, before: dict | None,
) -> DelegateResult:
    args = op.args
    mailbox = args["mailbox"]
    delegate = args["delegate"]
    access_rights = args.get("access_rights", "FullAccess")
    log_mutation_start(
        logger, op_id=op.op_id, cmd=f"mail-delegate-{action.lower()}",
        args=args, drive_id=op.drive_id, item_id=op.item_id, before=before or {},
    )
    try:
        rc, out, err = invoke_pwsh(_PS_SCRIPT, [
            "-Mailbox", mailbox, "-Action", action,
            "-Delegate", delegate, "-AccessRights", access_rights,
        ])
    except FileNotFoundError:
        log_mutation_end(
            logger, op_id=op.op_id, after={}, result="error", error=_PWSH_HINT,
        )
        return DelegateResult(op_id=op.op_id, status="error", error=_PWSH_HINT)
    if rc != 0:
        msg = err.strip() or out.strip() or f"pwsh exited with code {rc}"
        log_mutation_end(
            logger, op_id=op.op_id, after={}, result="error", error=msg,
        )
        return DelegateResult(op_id=op.op_id, status="error", error=msg)
    log_mutation_end(
        logger, op_id=op.op_id, after={"action": action, "mailbox": mailbox,
                                       "delegate": delegate,
                                       "access_rights": access_rights},
        result="ok",
    )
    return DelegateResult(
        op_id=op.op_id, status="ok",
        after={"action": action, "mailbox": mailbox, "delegate": delegate,
               "access_rights": access_rights},
    )
```

- [ ] **Step 3:** Run tests, mypy + ruff clean. Commit:
```
git add scripts/ps/Set-MailboxDelegate.ps1 \
        src/m365ctl/mail/mutate/delegate.py \
        tests/test_mail_mutate_delegate.py
git commit -m "feat(mail/mutate): delegate executors via Set-MailboxDelegate.ps1 (PnP/ExchangeOnline)"
```

### Task 2.3: Inverse registration (one commit)

Grant ↔ Revoke are natural inverses. Register them in `mail/mutate/undo.py` so `m365ctl undo <op-id>` rolls back a delegation change.

- [ ] **Step 1:** In `register_mail_inverses(dispatcher)`, add:
  - `mail.delegate.grant` inverse → `mail.delegate.revoke` with the same `mailbox` / `delegate` / `access_rights` args.
  - `mail.delegate.revoke` inverse → `mail.delegate.grant` with the same args.
- [ ] **Step 2:** Add audit-log branches in `build_reverse_mail_operation` for `mail-delegate-grant` ↔ `mail-delegate-revoke`.
- [ ] **Step 3:** Tests at `tests/test_mail_mutate_undo_delegate.py` (2 tests, one per direction).
- [ ] **Step 4:** Commit:
```
feat(mail/mutate/undo): register inverses for mail.delegate.grant ↔ revoke
```

---

## Group 3 — CLI: `mail delegate {list, grant, revoke}`

**Files:**
- Create: `src/m365ctl/mail/cli/delegate.py`
- Create: `bin/mail-delegate`
- Modify: `src/m365ctl/mail/cli/__main__.py`
- Create: `tests/test_cli_mail_delegate.py`

### Task 3.1 — CLI (one commit)

**CLI surface:**
```
mail delegate list <mailbox-upn>
mail delegate grant <mailbox-upn> --to <delegate-upn> [--rights FullAccess|SendAs|SendOnBehalf] --confirm
mail delegate revoke <mailbox-upn> --to <delegate-upn> [--rights FullAccess|SendAs|SendOnBehalf] --confirm
```

Notes:
- Mailbox argument is bare UPN (not `upn:`-prefixed), since this verb manages mailbox-level permissions and doesn't fit the read/write `--mailbox` pattern.
- `--rights` defaults to `FullAccess`.
- `--confirm` required for grant/revoke. Without → exit 2 with stderr.
- `list` always emits NDJSON on stdout (one DelegateEntry per line) when `--json`; otherwise human table.

- [ ] **Step 1:** Tests at `tests/test_cli_mail_delegate.py`:
  - `list <mailbox>` calls `list_delegates`, prints table.
  - `list <mailbox> --json` emits NDJSON.
  - `grant <mailbox> --to <upn> --rights FullAccess --confirm` calls `execute_grant` with the right args.
  - `grant <mailbox> --to <upn>` (no `--confirm`) returns 2.
  - `revoke <mailbox> --to <upn> --confirm` calls `execute_revoke`.
  - `grant <mailbox> --to <upn> --rights SendOnBehalf --confirm` passes `access_rights="SendOnBehalf"`.

- [ ] **Step 2:** Implement `src/m365ctl/mail/cli/delegate.py`:
  - argparse with subparsers `list`, `grant`, `revoke`. Positional `mailbox` UPN.
  - For `list`: print table or NDJSON.
  - For `grant` / `revoke`: build an `Operation`, call the matching executor, return 0 on success, 1 on error.
  - No Graph token needed (PowerShell connects independently to Exchange Online), so don't bother with `load_and_authorize` for the executors. The CLI still loads config to get `cfg.logging.ops_dir` for the audit logger.

- [ ] **Step 3:** Wire dispatcher: `mail/cli/__main__.py` add `elif verb == "delegate": from m365ctl.mail.cli.delegate import main as f`. Add `_USAGE` line:
  ```
  "  delegate     delegate list|grant|revoke (PnP.PowerShell — ExchangeOnline)\n"
  ```

- [ ] **Step 4:** Bin wrapper `bin/mail-delegate` + `chmod +x`.

- [ ] **Step 5:** Tests, gates, commit:
```
feat(mail/cli/delegate): mail delegate {list,grant,revoke} via PnP.PowerShell
```

---

## Group 4 — Release 1.2.0

### Task 4.1 — bump + changelog + README + lockfile (2 commits)

- [ ] `pyproject.toml`: 1.1.0 → 1.2.0.

- [ ] Prepend CHANGELOG.md:

```markdown
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
```

- [ ] README Mail bullet:
```markdown
- **Multi-mailbox + delegation (Phase 12, 1.2):** every shipped verb
  accepts `--mailbox shared:<addr>` for shared-mailbox routing.
  `mail delegate {list,grant,revoke} --rights …` manages FullAccess /
  SendAs / SendOnBehalf via Exchange Online PowerShell with audit + undo.
```

- [ ] `uv sync --all-extras`. Quality gates. Two release commits.

### Task 4.2 — push, PR, merge, tag

Push branch, open PR titled `Phase 12: multi-mailbox & delegation → 1.2.0`. Body summarises the helper consolidation, the integration coverage for `shared:`, the PowerShell delegation script, the mutator + undo wiring, and the new CLI surface. Test plan checklist. Watch CI, squash-merge, sync main, tag `v1.2.0`.

---

## Self-review

**Spec coverage (§19 Phase 12):**
- ✅ `m365ctl.mail.endpoints.user_base` handles `shared:<upn>` — verified existing + integration coverage in G1.2.
- ✅ App-only targeting of `/users/{upn}/messages` gated by `allow_mailboxes` — confirmed via existing `assert_mailbox_allowed`; tests added.
- ✅ `m365ctl.mail.mutate.delegate` via PnP.PowerShell (`scripts/ps/Set-MailboxDelegate.ps1` — generic) — G2.
- ✅ CLI: `mail-delegate {list, grant, revoke}` — G3.
- ✅ `--mailbox shared:…` works across all commands — G1.2.
- ⚠️ Spec acceptance "live smoke against a dedicated test shared mailbox" — flagged for the next live-smoke pass; not gated in CI.
- ⚠️ Spec said bump to 0.14.0 sequentially; we bump to 1.2.0 because we shipped 6/8/9/10/11/14 first.

**Type consistency:** `DelegateResult` follows the existing Result pattern (status/error/after). Audit API matches Phase 6/8/9. `derive_mailbox_upn` is the canonical name for the formerly-duplicated helper.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-25-phase-12-multi-mailbox.md`. Branch `phase-12-multi-mailbox` already off `main`.
