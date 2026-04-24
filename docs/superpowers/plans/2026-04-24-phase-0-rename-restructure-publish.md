# Phase 0 — Rename, Restructure, De-brand, Publish-Ready

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename `fazla_od` → `m365ctl`, restructure into `common/` + `onedrive/` + `mail/` sub-packages, strip every tenant-specific identifier, ship publishing artifacts (LICENSE, README, CONTRIBUTING, CI, setup docs), and extend config for the Mail domain — all behind a green `od-*` test suite. No mail behavior lands in Phase 0.

**Architecture:**
- Two-stage move: (1) `git mv src/fazla_od src/m365ctl` + sed-rewrite imports tree-wide, (2) split into `common/` + `onedrive/` sibling packages and add empty `mail/` scaffold.
- Undo becomes a domain-agnostic `Dispatcher` in `common/undo.py`; all `od.*` verbs register through it; legacy bare actions (pre-refactor audit entries) auto-prefix to `od.*` on read.
- Config grows mail-shaped fields (`MailConfig`, `allow_mailboxes`, `deny_folders`, `purged_dir`, `retention_days`) — defined but unused in Phase 0.
- Cache dir / env var / keychain labels rebrand with auto-migration where safely possible.
- Spec is docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md (repo root, working copy); final canonical home is `docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md` at end of Phase 0.

**Tech Stack:** Python 3.11+, httpx, msal, duckdb, hatchling, pytest, ruff, mypy, uv. PowerShell (scripts/ps), bash (bin/, scripts/setup/). GitHub Actions for CI.

**Parent spec:** `docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md` at repo root (canonicalized to `docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md` in Task 41). OneDrive technical conventions from `docs/superpowers/specs/2026-04-24-m365ctl-design.md` (renamed to `2026-04-24-m365ctl-design.md` in Task 41).

**Safety posture:**
- Work on a feature branch (`phase-0-m365ctl-rebrand`) — not directly on main.
- Every Task ends with `uv run pytest -m "not live"` green. A Task that leaves red tests is rolled back before the next Task starts.
- No force-push. No `git add -A`; stage explicit paths per commit.
- Keychain items (`FazlaODToolkit:*`) on the user's live machine are left untouched — user deletes them manually per migration doc (Task 37).

---

## File Structure (Phase 0 target)

```
m365ctl/
├── LICENSE                                    # NEW (Task 33)
├── README.md                                  # REWRITTEN (Task 34)
├── CONTRIBUTING.md                            # NEW (Task 35)
├── CHANGELOG.md                               # NEW (Task 38)
├── AGENTS.md                                  # REWRITTEN (Task 36)
├── docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md                            # remains at root for this session; canonicalized in Task 41
├── NEXT-SESSION.md                            # existing; updated to mark Phase 0 done (Task 47)
├── pyproject.toml                             # MODIFIED (Task 2)
├── config.toml.example                        # REWRITTEN (Task 29)
├── uv.lock                                    # regenerated if deps change (none in Phase 0)
├── .github/workflows/ci.yml                   # NEW (Task 39)
├── bin/                                       # RENAMED dispatchers (Task 30)
│   ├── od-auth, od-catalog-refresh, …         # all retargeted at `python -m m365ctl od <verb>`
│   ├── od-undo                                # DEPRECATED alias — 1-line notice then delegates
│   └── m365ctl-undo                           # NEW cross-domain undo
├── scripts/
│   ├── ps/                                    # rebranded (Tasks 13, 37)
│   │   ├── Set-M365ctlLabel.ps1               # renamed from Set-FazlaLabel.ps1
│   │   ├── _M365ctlRecycleHelpers.ps1         # renamed from _FazlaRecycleHelpers.ps1
│   │   ├── audit-sharing.ps1, convert-cert.sh, recycle-*.ps1
│   └── setup/
│       └── create-cert.sh                     # NEW (Task 35b)
├── src/m365ctl/
│   ├── __init__.py                            # version string
│   ├── __main__.py                            # `python -m m365ctl` entry
│   ├── common/                                # moved (Task 6) — auth, graph, config, audit, safety, retry, planfile, undo
│   ├── onedrive/                              # moved (Task 7) — catalog/, download/, mutate/, search/, cli/
│   ├── mail/                                  # NEW scaffold only (Task 25) — empty packages; filled by Phase 1+
│   └── cli/                                   # NEW top-level dispatcher (Task 23) — `m365ctl <domain> <verb>` + `m365ctl undo`
├── tests/                                     # imports sed-rewritten (Task 4)
└── docs/
    ├── ops/pnp-powershell-setup.md            # UUID-redacted (Task 14)
    ├── setup/                                 # NEW
    │   ├── azure-app-registration.md          # NEW (Task 35c)
    │   ├── certificate-auth.md                # NEW (Task 35d)
    │   ├── first-run.md                       # NEW (Task 35e)
    │   └── migrating-from-fazla-od.md         # NEW (Task 37)
    └── superpowers/
        ├── specs/
        │   ├── 2026-04-24-m365ctl-design.md         # renamed (Task 41a)
        │   └── 2026-04-24-m365ctl-mail-module.md    # copied from docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md (Task 41b)
        └── plans/
            ├── 2026-04-24-phase-0-rename-restructure-publish.md   # this file
            └── (existing Phase -1 plans)        # UUID-redacted (Task 42)
```

---

## Preflight

### Task 0: Branch + baseline

**Files:** none (git state only)

- [ ] **Step 1: Confirm clean working tree except for the untracked spec**

Run: `git status`
Expected: Only untracked `docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md` shown. If anything else is modified or untracked, stop and resolve with user before proceeding.

- [ ] **Step 2: Create Phase 0 branch**

Run: `git checkout -b phase-0-m365ctl-rebrand`
Expected: `Switched to a new branch 'phase-0-m365ctl-rebrand'`.

- [ ] **Step 3: Capture baseline test count**

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: all green; note exact pass count (e.g. `287 passed in 12.3s`). This number must not drop across any subsequent Task.

- [ ] **Step 4: Snapshot the fazla reference footprint**

Run:
```bash
{
  grep -rlI 'fazla_od\|FazlaOD\|FAZLA_OD\|Fazla OneDrive\|Fazla M365\|fazla-od' \
    src/ tests/ bin/ scripts/ docs/ pyproject.toml README.md AGENTS.md config.toml.example 2>/dev/null;
  grep -rnI '361efb70\|b22e6fd3\|C38CC9B49D5E4D326B4A79ECAF33CD65B008BCBF' \
    src/ tests/ bin/ scripts/ docs/ pyproject.toml README.md AGENTS.md config.toml.example 2>/dev/null;
} > /tmp/m365ctl-phase0-baseline.txt
wc -l /tmp/m365ctl-phase0-baseline.txt
```
Expected: a non-empty count (~85 files / ~20 UUID refs). Keep this file — Task 46 asserts the sweep leaves nothing beyond the documented exceptions.

---

## Part A — Rename & restructure

### Task 1: Pyproject rename

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Rewrite metadata + script entry + hatch target + pytest marker**

Edit `pyproject.toml`:
- `name = "fazla-od"` → `name = "m365ctl"`
- `version = "0.1.0"` stays (spec §18.8 — 0.1.0 covers Phase 0).
- `description = "CLI toolkit for admin-scoped control of the Fazla M365 OneDrive + SharePoint tenant via Microsoft Graph."` → `description = "Admin CLI for Microsoft 365 OneDrive + SharePoint + Mail via Microsoft Graph."`
- `[project.scripts] fazla-od = "fazla_od.cli.__main__:main"` → `m365ctl = "m365ctl.cli.__main__:main"`
- `[tool.hatch.build.targets.wheel] packages = ["src/fazla_od"]` → `packages = ["src/m365ctl"]`
- Pytest marker: `"live: hits real Microsoft Graph; requires FAZLA_OD_LIVE_TESTS=1"` → `"live: hits real Microsoft Graph; requires M365CTL_LIVE_TESTS=1"`

- [ ] **Step 2: Collect still works (no moves yet, so this should just parse)**

Run: `uv run pytest --collect-only -q 2>&1 | tail -3`
Expected: same collected count as Task 0 Step 3. Does not run tests yet because `m365ctl.cli.__main__:main` doesn't exist — entry-point is installed lazily.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore(pyproject): rename package fazla-od -> m365ctl (marker, script, hatch target)"
```

---

### Task 2: Top-level git mv + tree-wide import rewrite

**Files:**
- Move: `src/fazla_od/` → `src/m365ctl/`
- Modify: every file matching `fazla_od` — `src/`, `tests/`, `bin/`, `scripts/`

- [ ] **Step 1: Move the package directory**

Run: `git mv src/fazla_od src/m365ctl`
Expected: no output; `ls src/` shows `m365ctl` only.

- [ ] **Step 2: Purge stale pycache under the renamed tree**

Run: `find src/m365ctl -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; true`
Expected: silent.

- [ ] **Step 3: Rewrite `fazla_od` → `m365ctl` across code-bearing trees**

Run:
```bash
grep -rlI --null 'fazla_od' src/ tests/ bin/ scripts/ 2>/dev/null \
  | xargs -0 sed -i '' 's/fazla_od/m365ctl/g'
```
(On Linux: drop the `''` argument to `sed -i`.)

Expected: silent success.

- [ ] **Step 4: Confirm zero fazla_od references in code trees**

Run: `grep -rn 'fazla_od' src/ tests/ bin/ scripts/ 2>/dev/null | wc -l`
Expected: `0`.

- [ ] **Step 5: Run the full non-live suite**

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: pass count matches Task 0 baseline. Any failure: `git restore` + investigate before proceeding.

- [ ] **Step 6: Commit**

```bash
git add -A src/ tests/ bin/ scripts/
git commit -m "refactor: rename package fazla_od -> m365ctl (mechanical import rewrite)"
```

---

### Task 3: Carve out `common/` sub-package

**Files:**
- Move: `src/m365ctl/{auth,graph,config,audit,safety,retry,planfile,prompts}.py` → `src/m365ctl/common/`
- Keep: `src/m365ctl/mutate/undo.py` stays in `mutate/` for now (Task 20 moves logic into `common/undo.py`)
- Tree-wide import rewrite

- [ ] **Step 1: Create the sub-package**

Run: `mkdir -p src/m365ctl/common`

- [ ] **Step 2: Move shared modules via `git mv`**

```bash
for m in auth graph config audit safety retry planfile prompts; do
  git mv src/m365ctl/$m.py src/m365ctl/common/$m.py
done
```

- [ ] **Step 3: Seed `common/__init__.py`**

Write `src/m365ctl/common/__init__.py`:
```python
"""Shared infrastructure for m365ctl: auth, Graph client, config, audit, safety, retry, planfile."""
```

- [ ] **Step 4: Rewrite imports tree-wide**

Run:
```bash
grep -rlI --null 'm365ctl\.\(auth\|graph\|config\|audit\|safety\|retry\|planfile\|prompts\)\|from m365ctl import \(auth\|graph\|config\|audit\|safety\|retry\|planfile\|prompts\)' \
  src/ tests/ bin/ scripts/ 2>/dev/null \
  | xargs -0 python3 -c '
import sys, re, pathlib
pat = re.compile(r"\bm365ctl\.(auth|graph|config|audit|safety|retry|planfile|prompts)\b")
for p in sys.argv[1:]:
    pth = pathlib.Path(p)
    src = pth.read_text()
    new = pat.sub(r"m365ctl.common.\1", src)
    if new != src:
        pth.write_text(new)
' --
```

- [ ] **Step 5: Verify no top-level imports leaked**

Run:
```bash
grep -rnE 'from m365ctl\.(auth|graph|config|audit|safety|retry|planfile|prompts) import|import m365ctl\.(auth|graph|config|audit|safety|retry|planfile|prompts)\b' \
  src/ tests/ bin/ scripts/ 2>/dev/null
```
Expected: empty.

- [ ] **Step 6: Run tests**

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: baseline pass count.

- [ ] **Step 7: Commit**

```bash
git add -A src/ tests/ bin/ scripts/
git commit -m "refactor(m365ctl): carve out common/ sub-package (auth, graph, config, audit, safety, retry, planfile, prompts)"
```

---

### Task 4: Carve out `onedrive/` sub-package

**Files:**
- Move: `src/m365ctl/{catalog,download,mutate,search,cli}/` → `src/m365ctl/onedrive/`
- Tree-wide import rewrite

- [ ] **Step 1: Create the sub-package**

Run: `mkdir -p src/m365ctl/onedrive`

- [ ] **Step 2: Move each domain-specific sub-package**

```bash
for d in catalog download mutate search cli; do
  git mv src/m365ctl/$d src/m365ctl/onedrive/$d
done
```

- [ ] **Step 3: Seed `onedrive/__init__.py`**

Write `src/m365ctl/onedrive/__init__.py`:
```python
"""OneDrive + SharePoint domain: catalog, download, mutate, search, CLI."""
```

- [ ] **Step 4: Rewrite imports**

Run:
```bash
grep -rlI --null 'm365ctl\.\(catalog\|download\|mutate\|search\|cli\)\|from m365ctl import \(catalog\|download\|mutate\|search\|cli\)' \
  src/ tests/ bin/ scripts/ 2>/dev/null \
  | xargs -0 python3 -c '
import sys, re, pathlib
pat = re.compile(r"\bm365ctl\.(catalog|download|mutate|search|cli)\b")
for p in sys.argv[1:]:
    pth = pathlib.Path(p)
    src = pth.read_text()
    new = pat.sub(r"m365ctl.onedrive.\1", src)
    if new != src:
        pth.write_text(new)
' --
```

- [ ] **Step 5: Fix `src/m365ctl/__main__.py`**

Existing content: `from m365ctl.cli.__main__ import main` — after the move, `m365ctl.cli` no longer exists at top level until Task 23. For now, rewrite to:
```python
from m365ctl.onedrive.cli.__main__ import main

if __name__ == "__main__":
    main()
```
(This keeps `python -m m365ctl` working on the OneDrive surface until Task 23 replaces it with the cross-domain dispatcher.)

- [ ] **Step 6: Verify no top-level imports leaked**

Run: `grep -rnE 'from m365ctl\.(catalog|download|mutate|search|cli)\b|import m365ctl\.(catalog|download|mutate|search|cli)\b' src/ tests/ bin/ scripts/ 2>/dev/null`
Expected: empty (cli/ imports internal to onedrive/cli/ use relative imports already, but double-check anything under src/m365ctl/onedrive/cli/__main__.py references sibling modules as `m365ctl.onedrive.cli.*`).

- [ ] **Step 7: Run tests**

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: baseline pass count.

- [ ] **Step 8: Update bin wrappers to reflect new module paths**

The existing `bin/od-*` wrappers invoke `python -m fazla_od.cli <verb>` (post-Task 2: `python -m m365ctl.cli <verb>`). After the move, `m365ctl.cli` doesn't exist. Tree-rewrite `bin/od-*`:

Run:
```bash
sed -i '' 's|python -m m365ctl\.cli|python -m m365ctl.onedrive.cli|g' bin/od-*
```
(Linux: drop `''`.)

Run `head -n 10 bin/od-search bin/od-auth` to confirm the rewrite.

- [ ] **Step 9: Smoke a CLI invocation**

Run: `uv run python -m m365ctl.onedrive.cli --help 2>&1 | head -20`
Expected: the current fazla-od help banner (text still says "fazla-od" — fixed in Task 11). Command dispatches without ImportError.

- [ ] **Step 10: Commit**

```bash
git add -A src/ tests/ bin/ scripts/
git commit -m "refactor(m365ctl): carve out onedrive/ sub-package (catalog, download, mutate, search, cli)"
```

---

### Task 5: Rename `mutate/_pwsh.py` import audit

**Files:**
- Modify: `src/m365ctl/onedrive/mutate/_pwsh.py` (if it embeds a path reference like `scripts/ps/_FazlaRecycleHelpers.ps1`)

- [ ] **Step 1: Search for hardcoded PS script names**

Run: `grep -n 'FazlaRecycleHelpers\|Set-FazlaLabel\|_Fazla' src/m365ctl/onedrive/mutate/_pwsh.py src/m365ctl/onedrive/mutate/*.py`
Expected: any hits get rewritten to reference `_M365ctlRecycleHelpers.ps1` / `Set-M365ctlLabel.ps1` (scripts renamed in Task 13). If hits list, record them; defer actual rewrite to Task 13 Step 4 so the rename + code-update land in one commit.

- [ ] **Step 2: No changes here — Task 13 closes this loop.**

*(Zero-cost audit step. Left in the plan so the reader sees the coupling.)*

---

## Part A (cont.) — Top-level CLI dispatcher + mail scaffold

### Task 6: New top-level `m365ctl.cli` dispatcher

**Files:**
- Create: `src/m365ctl/cli/__init__.py`
- Create: `src/m365ctl/cli/__main__.py`
- Create: `tests/test_top_cli.py`

- [ ] **Step 1: Failing test — top-level dispatcher routes `od <verb>` to onedrive.cli**

Write `tests/test_top_cli.py`:
```python
"""Smoke tests for the top-level m365ctl CLI dispatcher."""
from __future__ import annotations

import subprocess
import sys


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "m365ctl", *args],
        capture_output=True, text=True, check=False,
    )


def test_top_level_help_lists_both_domains():
    r = _run(["--help"])
    assert r.returncode == 0
    out = r.stdout + r.stderr
    assert "od" in out
    # `mail` is listed even though Phase 0 ships no mail verbs.
    assert "mail" in out
    assert "undo" in out


def test_od_search_passthrough_reaches_onedrive_cli():
    # --help on the od sub-dispatcher should not error.
    r = _run(["od", "--help"])
    assert r.returncode == 0
    assert "search" in (r.stdout + r.stderr)


def test_unknown_domain_exits_nonzero():
    r = _run(["teams", "foo"])
    assert r.returncode != 0


def test_mail_domain_exists_but_has_no_verbs_yet():
    # Phase 0: mail tree is scaffold only. `mail --help` should print a
    # "not yet implemented" notice and exit non-zero, not ImportError.
    r = _run(["mail"])
    assert r.returncode != 0
    assert "not yet" in (r.stdout + r.stderr).lower() or "phase 1" in (r.stdout + r.stderr).lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_top_cli.py -q 2>&1 | tail -10`
Expected: 4 FAILED — `m365ctl.cli` package missing / unresolved.

- [ ] **Step 3: Implement the dispatcher**

Write `src/m365ctl/cli/__init__.py`:
```python
"""Top-level m365ctl CLI dispatcher: routes to onedrive.cli, mail.cli, or undo."""
```

Write `src/m365ctl/cli/__main__.py`:
```python
"""m365ctl <domain> <verb> — cross-domain CLI entry point."""
from __future__ import annotations

import sys

_USAGE = (
    "usage: m365ctl <domain> <verb> [args...]\n"
    "       m365ctl undo <op-id> [--confirm]\n"
    "\n"
    "Domains:\n"
    "  od     OneDrive + SharePoint (catalog, search, move, copy, delete, …)\n"
    "  mail   Microsoft 365 Mail — reserved for Phase 1+; no verbs yet\n"
    "  undo   Cross-domain audit-log replay (od.* and mail.*)\n"
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(_USAGE)
        return 0 if args else 1
    domain = args[0]
    rest = args[1:]
    if domain == "od":
        from m365ctl.onedrive.cli.__main__ import main as od_main
        return od_main(rest) or 0
    if domain == "mail":
        print(
            "m365ctl: mail domain is not yet implemented — scaffold only. "
            "See docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md §19 Phase 1 for delivery target.",
            file=sys.stderr,
        )
        return 2
    if domain == "undo":
        from m365ctl.cli.undo import main as undo_main
        return undo_main(rest) or 0
    print(f"m365ctl: unknown domain {domain!r}\n\n{_USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Give `onedrive.cli.__main__.main` an `argv` parameter**

Open `src/m365ctl/onedrive/cli/__main__.py`. Current signature: `def main() -> int:` using `sys.argv` directly. Change to:
```python
def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # … existing dispatch uses `args` instead of `sys.argv[1:]` …
```
Preserve all existing subcommand routing logic. If existing code reads `sys.argv[0]` for the progname in help text, hardcode it to `m365ctl od` instead.

Also rewrite `usage: fazla-od <subcommand> [args...]` → `usage: m365ctl od <subcommand> [args...]` (per Task 11's broader sweep; do it now since we're in the file).

- [ ] **Step 5: Stub `m365ctl.cli.undo`**

Write `src/m365ctl/cli/undo.py`:
```python
"""m365ctl undo — thin delegate; full dispatcher lands in Task 20."""
from __future__ import annotations

from m365ctl.onedrive.cli.undo import main as _onedrive_undo_main


def main(argv: list[str] | None = None) -> int:
    # Pre-Task 20 shim: the existing OneDrive undo already handles all
    # `od.*` audit entries (and legacy bare actions). Task 20 replaces this
    # with a domain-agnostic Dispatcher.
    return _onedrive_undo_main(argv) or 0
```

- [ ] **Step 6: Patch the package `__main__` to go through the top-level dispatcher**

Overwrite `src/m365ctl/__main__.py`:
```python
from m365ctl.cli.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Run the new tests**

Run: `uv run pytest tests/test_top_cli.py -q 2>&1 | tail -10`
Expected: 4 PASSED.

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: baseline + 4 (new tests from Step 1).

- [ ] **Step 9: Commit**

```bash
git add src/m365ctl/cli/ src/m365ctl/__main__.py src/m365ctl/onedrive/cli/__main__.py tests/test_top_cli.py
git commit -m "feat(cli): top-level m365ctl dispatcher routes od/mail/undo domains"
```

---

### Task 7: Mail package scaffold (empty)

**Files:**
- Create: `src/m365ctl/mail/__init__.py` (+ every sub-package per spec §4.3)

- [ ] **Step 1: Create the tree**

Run:
```bash
mkdir -p src/m365ctl/mail/{catalog,mutate,triage,cli}
```

- [ ] **Step 2: Seed all `__init__.py` files**

Write the following files (each with the one-line docstring shown):

- `src/m365ctl/mail/__init__.py` — `"""Mail domain — scaffold only in Phase 0. Implementation lands in Phase 1+."""`
- `src/m365ctl/mail/catalog/__init__.py` — `"""Mail local catalog (DuckDB)."""`
- `src/m365ctl/mail/mutate/__init__.py` — `"""Mail mutations — all audit- and undo-instrumented."""`
- `src/m365ctl/mail/triage/__init__.py` — `"""Mail triage DSL and plan emitter."""`
- `src/m365ctl/mail/cli/__init__.py` — `"""Mail CLI verb modules."""`

- [ ] **Step 3: Import smoke**

Run: `uv run python -c "import m365ctl.mail, m365ctl.mail.catalog, m365ctl.mail.mutate, m365ctl.mail.triage, m365ctl.mail.cli; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Run full suite**

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: baseline + 4.

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/mail/
git commit -m "feat(mail): scaffold mail/ sub-package tree (empty; filled by Phase 1+)"
```

---

## Part B — De-branding

### Task 8: Cache directory + auto-migration

**Files:**
- Modify: `src/m365ctl/common/auth.py` (lines 31–32, 97–108)
- Modify: `tests/test_auth.py` (if any path assertions)

- [ ] **Step 1: Failing test — new cache path + legacy auto-migration**

Append to `tests/test_auth.py`:
```python
def test_load_persistent_cache_migrates_legacy_fazla_od_dir(tmp_path, monkeypatch):
    from m365ctl.common import auth as auth_mod
    fake_home = tmp_path
    monkeypatch.setattr(auth_mod.Path, "home", staticmethod(lambda: fake_home))
    # Re-evaluate the module-level constants against the patched home.
    monkeypatch.setattr(auth_mod, "_CACHE_DIR", fake_home / ".config" / "m365ctl")
    monkeypatch.setattr(auth_mod, "_CACHE_FILE", fake_home / ".config" / "m365ctl" / "token_cache.bin")
    monkeypatch.setattr(auth_mod, "_LEGACY_CACHE_DIR", fake_home / ".config" / "fazla-od")
    monkeypatch.setattr(auth_mod, "_LEGACY_CACHE_FILE", fake_home / ".config" / "fazla-od" / "token_cache.bin")
    legacy = fake_home / ".config" / "fazla-od"
    legacy.mkdir(parents=True)
    (legacy / "token_cache.bin").write_text("{}")
    cache = auth_mod._load_persistent_cache()
    assert cache is not None
    assert (fake_home / ".config" / "m365ctl" / "token_cache.bin").exists()
    # Legacy file moved, not copied.
    assert not (legacy / "token_cache.bin").exists()


def test_load_persistent_cache_returns_empty_when_no_cache(tmp_path, monkeypatch):
    from m365ctl.common import auth as auth_mod
    monkeypatch.setattr(auth_mod, "_CACHE_DIR", tmp_path / ".config" / "m365ctl")
    monkeypatch.setattr(auth_mod, "_CACHE_FILE", tmp_path / ".config" / "m365ctl" / "token_cache.bin")
    monkeypatch.setattr(auth_mod, "_LEGACY_CACHE_DIR", tmp_path / ".config" / "fazla-od")
    monkeypatch.setattr(auth_mod, "_LEGACY_CACHE_FILE", tmp_path / ".config" / "fazla-od" / "token_cache.bin")
    cache = auth_mod._load_persistent_cache()
    # msal returns an empty SerializableTokenCache when nothing on disk.
    assert cache is not None
    assert not (tmp_path / ".config" / "m365ctl" / "token_cache.bin").exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_auth.py::test_load_persistent_cache_migrates_legacy_fazla_od_dir -q`
Expected: FAIL — `_LEGACY_CACHE_DIR` not defined.

- [ ] **Step 3: Update `src/m365ctl/common/auth.py`**

Replace the existing cache constants (currently `_CACHE_DIR = Path.home() / ".config" / "fazla-od"`) with:
```python
_CACHE_DIR = Path.home() / ".config" / "m365ctl"
_CACHE_FILE = _CACHE_DIR / "token_cache.bin"
_LEGACY_CACHE_DIR = Path.home() / ".config" / "fazla-od"
_LEGACY_CACHE_FILE = _LEGACY_CACHE_DIR / "token_cache.bin"
```

Replace `_load_persistent_cache`:
```python
def _load_persistent_cache() -> msal.SerializableTokenCache:
    """Load the MSAL token cache, migrating any legacy ~/.config/fazla-od/ file.

    The cache file sits at mode 600 inside ~/.config/m365ctl/ (mode 700).
    On first run after the rebrand, we opportunistically move a pre-existing
    fazla-od token cache into the new location. If the move fails (permission
    issue, already-exists race), we log and fall back to a clean login —
    safer than deserializing from the legacy file and leaving the two paths
    divergent.
    """
    cache = msal.SerializableTokenCache()
    if _CACHE_FILE.exists():
        cache.deserialize(_CACHE_FILE.read_text())
        return cache
    if _LEGACY_CACHE_FILE.exists():
        try:
            _CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            _LEGACY_CACHE_FILE.rename(_CACHE_FILE)
            os.chmod(_CACHE_FILE, 0o600)
            cache.deserialize(_CACHE_FILE.read_text())
        except OSError as e:
            import sys
            print(
                f"m365ctl: could not migrate legacy token cache from "
                f"{_LEGACY_CACHE_FILE} ({e}); re-run `m365ctl od auth login`.",
                file=sys.stderr,
            )
    return cache
```

Update the docstring on `_load_persistent_cache` / callers that reference `~/.config/fazla-od`: replace with `~/.config/m365ctl`.

- [ ] **Step 4: Run the new tests + full suite**

Run: `uv run pytest tests/test_auth.py -q`
Expected: PASS.

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: baseline + 6 (top-cli + 2 new auth tests).

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/common/auth.py tests/test_auth.py
git commit -m "refactor(auth): move token cache to ~/.config/m365ctl/ with legacy auto-migration"
```

---

### Task 9: Environment variable rename — `FAZLA_OD_LIVE_TESTS` → `M365CTL_LIVE_TESTS`

**Files:**
- Modify: `pyproject.toml` (already handled in Task 2)
- Modify: any test that reads `FAZLA_OD_LIVE_TESTS`

- [ ] **Step 1: Find references**

Run: `grep -rn 'FAZLA_OD_LIVE_TESTS\|M365CTL_LIVE_TESTS' src/ tests/ bin/ scripts/ docs/ 2>/dev/null`
Expected: zero or a handful (typically in conftest.py or test skip markers).

- [ ] **Step 2: Rewrite + add deprecation shim**

For each hit that reads the env var: accept either name, but if `FAZLA_OD_LIVE_TESTS` is set and `M365CTL_LIVE_TESTS` is not, print a one-line stderr warning and adopt the legacy value. Example `tests/conftest.py` pattern (add if missing):
```python
import os
import sys


def _live_tests_enabled() -> bool:
    new = os.environ.get("M365CTL_LIVE_TESTS")
    legacy = os.environ.get("FAZLA_OD_LIVE_TESTS")
    if new:
        return new == "1"
    if legacy:
        print(
            "m365ctl: FAZLA_OD_LIVE_TESTS is deprecated; set M365CTL_LIVE_TESTS=1 instead.",
            file=sys.stderr,
        )
        return legacy == "1"
    return False
```
Wire it to whatever `pytest.mark.skipif(...)` call currently gates live tests.

- [ ] **Step 3: Verify no bare `FAZLA_OD_LIVE_TESTS` reference remains outside the shim**

Run: `grep -rn 'FAZLA_OD_LIVE_TESTS' src/ tests/ bin/ scripts/ docs/ 2>/dev/null | grep -v 'deprecated'`
Expected: empty.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: unchanged pass count.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py  # or wherever the shim lives
git commit -m "refactor(tests): M365CTL_LIVE_TESTS with FAZLA_OD_LIVE_TESTS deprecation shim"
```

---

### Task 10: Purge tenant UUIDs + cert thumbprint from tracked files (non-plan/spec)

**Files:**
- Modify: `config.toml.example` — rewritten entirely in Task 29; skip here.
- Modify: `tests/test_config.py` (lines 12–13, 38–39 — replace UUIDs with placeholder `00000000-...`).
- Modify: `docs/ops/pnp-powershell-setup.md` (lines 58, 94–95).
- Modify: `scripts/ps/audit-sharing.ps1` (line 26).

(Plans under `docs/superpowers/plans/*.md` and the parent spec are handled in Task 42.)

- [ ] **Step 1: Replace UUIDs in test_config.py**

In `tests/test_config.py`, replace every occurrence of `361efb70-ca20-41ae-b204-9045df001350` with `00000000-0000-0000-0000-000000000000` and every `b22e6fd3-4859-43ae-b997-997ad3aaf14b` with `11111111-1111-1111-1111-111111111111`. Two distinct placeholders make assertion equality meaningful.

- [ ] **Step 2: Scrub docs/ops/pnp-powershell-setup.md + scripts/ps/audit-sharing.ps1**

Replace tenant UUID with `<your-tenant-id>`, client UUID with `<your-client-id>`, thumbprint with `<your-cert-thumbprint>` in both files.

- [ ] **Step 3: Verify tests still pass**

Run: `uv run pytest tests/test_config.py -q`
Expected: PASS (assertions now compare against the placeholder UUIDs).

- [ ] **Step 4: Run full suite**

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: baseline + 6.

- [ ] **Step 5: Commit**

```bash
git add tests/test_config.py docs/ops/pnp-powershell-setup.md scripts/ps/audit-sharing.ps1
git commit -m "refactor: strip tenant + client UUIDs + cert thumbprint from code/tests/ops docs"
```

---

### Task 11: Docstring + user-facing string sweep

**Files:**
- Modify: any `.py` file containing `Fazla OneDrive Toolkit`, `Fazla M365`, `fazla-od` (in messages/docstrings), or `fazla_od` (in docstrings).

- [ ] **Step 1: Find residual user-facing branding strings**

Run:
```bash
grep -rn 'Fazla OneDrive\|Fazla M365\|fazla_od\|fazla-od\|FazlaOD' src/ 2>/dev/null
```
Expected: docstring hits (e.g. `src/m365ctl/common/config.py:1`, `src/m365ctl/common/auth.py:1`, `src/m365ctl/onedrive/cli/auth.py:27`).

- [ ] **Step 2: Rewrite each hit**

Per spec §4.2 mapping:
- `"Fazla OneDrive Toolkit"` → `"m365ctl"`
- `"Fazla M365 tenant"` → `"Microsoft 365 tenant"`
- `"fazla_od"` (in docstrings/comments) → `"m365ctl"`
- `"fazla-od"` (in help output / error messages) → `"m365ctl"`

Specifically:
- `src/m365ctl/common/config.py:1` docstring — update to `"""TOML-backed configuration loader for m365ctl."""`
- `src/m365ctl/common/auth.py:1` docstring — `"""MSAL-backed authentication for m365ctl."""`
- `src/m365ctl/onedrive/cli/auth.py:27` whoami banner — `print("m365ctl")`
- `src/m365ctl/onedrive/cli/__main__.py:40` usage line — `print("usage: m365ctl od <subcommand> [args...]")` (already done in Task 6 Step 4 if that branch landed; verify)
- Error messages like `run `od-auth login`` — left as-is (the short wrapper still exists).

- [ ] **Step 3: Verify user-facing sweep**

Run: `grep -rn 'Fazla\|fazla' src/ 2>/dev/null`
Expected: empty.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: baseline + 6. (If `tests/test_cli_auth.py` asserts `"FazlaODToolkit"` in whoami output, update the assertion to allow either — or see Task 12.)

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/
git commit -m "refactor: strip Fazla branding from code docstrings and user-facing strings"
```

---

### Task 12: Fix `test_cli_auth.py` — cert subject expectation

**Files:**
- Modify: `tests/test_cli_auth.py` (lines 24, 34, 44, 73, 81).

- [ ] **Step 1: Re-examine the failing assertions**

Current fixtures set `app_only.cert_info.subject = "CN=FazlaODToolkit"` and assert `"FazlaODToolkit" in out`. These pin the whoami output to a cert CN that, post-rebrand, will be `CN=m365ctl`. The test itself shouldn't care — it just wants the CN round-tripped. Rewrite to use a neutral fixture value.

- [ ] **Step 2: Replace all five hits**

Change `"CN=FazlaODToolkit"` → `"CN=m365ctl-test"` and `"FazlaODToolkit"` → `"m365ctl-test"` in all five places.

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_cli_auth.py -q`
Expected: PASS.

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: baseline + 6.

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli_auth.py
git commit -m "test(cli_auth): use neutral 'm365ctl-test' cert subject fixture"
```

---

### Task 13: PowerShell script rename + keychain label update

**Files:**
- Move: `scripts/ps/Set-FazlaLabel.ps1` → `scripts/ps/Set-M365ctlLabel.ps1`
- Move: `scripts/ps/_FazlaRecycleHelpers.ps1` → `scripts/ps/_M365ctlRecycleHelpers.ps1`
- Modify: `scripts/ps/audit-sharing.ps1`, `scripts/ps/convert-cert.sh`, and any Python caller in `src/m365ctl/onedrive/mutate/_pwsh.py` or siblings that references the old filenames.

- [ ] **Step 1: Rename the scripts**

```bash
git mv scripts/ps/Set-FazlaLabel.ps1 scripts/ps/Set-M365ctlLabel.ps1
git mv scripts/ps/_FazlaRecycleHelpers.ps1 scripts/ps/_M365ctlRecycleHelpers.ps1
```

- [ ] **Step 2: Rewrite keychain service names + internal references**

Tree-rewrite every PowerShell + bash reference:
```bash
grep -rlI 'FazlaODToolkit:\|_FazlaRecycleHelpers\|Set-FazlaLabel\|FazlaODToolkit"' \
  scripts/ src/ tests/ docs/ 2>/dev/null \
  | xargs sed -i '' \
    -e 's|FazlaODToolkit:|m365ctl:|g' \
    -e 's|_FazlaRecycleHelpers|_M365ctlRecycleHelpers|g' \
    -e 's|Set-FazlaLabel|Set-M365ctlLabel|g' \
    -e 's|"FazlaODToolkit"|"m365ctl"|g' \
    -e 's|Get-FazlaPfxPassword|Get-M365ctlPfxPassword|g'
```
(Linux: drop `''`.)

- [ ] **Step 3: Verify PS internal function names match**

Run: `grep -n 'FazlaPfxPassword\|_Fazla\|Fazla' scripts/ps/*.ps1 scripts/ps/*.sh`
Expected: empty.

- [ ] **Step 4: Update Python refs to PS script filenames**

Run: `grep -rn 'Set-FazlaLabel\|_FazlaRecycleHelpers' src/m365ctl/ 2>/dev/null`
Expected: empty (Step 2 also covered `src/`). If anything sneaks by, rewrite by hand.

- [ ] **Step 5: Run tests**

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: baseline + 6. (`tests/test_mutate_label.py` or similar may mock subprocess calls that name the PS script — if an assertion hard-codes the old filename, fix it.)

- [ ] **Step 6: Commit**

```bash
git add -A scripts/ src/ tests/ docs/
git commit -m "refactor(ps): rename Fazla* PowerShell helpers to M365ctl*; keychain service renamed"
```

---

### Task 14: README.md sweep (placeholder pass)

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Neuter branding now; full rewrite happens in Task 34**

Open `README.md`. Do a minimal search-replace: every `Fazla` → `m365ctl`, every `fazla-od` → `m365ctl`, every `fazla_od` → `m365ctl`. Do not restructure content yet.

- [ ] **Step 2: Grep-verify**

Run: `grep -n 'Fazla\|fazla' README.md`
Expected: empty.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): placeholder de-brand (full rewrite in Task 34)"
```

---

### Task 15: AGENTS.md sweep (placeholder pass)

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Replace branding inline**

In `AGENTS.md`: `Fazla` → `m365ctl`, `fazla-od` → `m365ctl`, `fazla_od` → `m365ctl`. Also rewrite any mention of "OneDrive toolkit" to "m365ctl (OneDrive + Mail CLI)".

- [ ] **Step 2: Grep-verify**

Run: `grep -n 'Fazla\|fazla' AGENTS.md`
Expected: empty.

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): de-brand references (full rewrite deferred to Task 36)"
```

---

### Task 16: `pyproject.toml` `[tool.pytest.ini_options]` marker + description final sweep

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Grep-verify no remaining fazla strings in pyproject**

Run: `grep -n 'Fazla\|fazla' pyproject.toml`
Expected: empty (Task 2 should have handled it; this is the final check). If anything remains, fix now.

- [ ] **Step 2: Commit (no-op commit only if changes made)**

If Step 1 found anything:
```bash
git add pyproject.toml
git commit -m "chore(pyproject): residual fazla reference sweep"
```

Otherwise skip the commit.

---

## Part C — Undo dispatcher (common/undo.py)

### Task 17: `common/undo.Dispatcher` — failing tests

**Files:**
- Create: `tests/test_common_undo_dispatcher.py`

- [ ] **Step 1: Write the test module**

Write `tests/test_common_undo_dispatcher.py`:
```python
"""Unit tests for the domain-agnostic undo dispatcher."""
from __future__ import annotations

import pytest

from m365ctl.common.undo import (
    Dispatcher,
    IrreversibleOp,
    UnknownAction,
    normalize_legacy_action,
)


def test_register_and_invoke_reversible_action():
    d = Dispatcher()
    calls: list[dict] = []
    def rename_inverse(before: dict, after: dict) -> dict:
        calls.append({"before": before, "after": after})
        return {"action": "od.rename", "args": {"new_name": before["old_name"]}}
    d.register("od.rename", rename_inverse)
    inv = d.build_inverse("od.rename", before={"old_name": "A"}, after={"new_name": "B"})
    assert inv == {"action": "od.rename", "args": {"new_name": "A"}}
    assert calls == [{"before": {"old_name": "A"}, "after": {"new_name": "B"}}]


def test_irreversible_registration_raises_on_build():
    d = Dispatcher()
    d.register_irreversible("mail.send", "Sent mail cannot be recalled programmatically.")
    with pytest.raises(IrreversibleOp) as excinfo:
        d.build_inverse("mail.send", before={}, after={})
    assert "Sent mail cannot be recalled" in str(excinfo.value)


def test_unknown_action_raises():
    d = Dispatcher()
    with pytest.raises(UnknownAction):
        d.build_inverse("teams.chat.send", before={}, after={})


def test_double_register_raises():
    d = Dispatcher()
    d.register("od.move", lambda b, a: {})
    with pytest.raises(ValueError):
        d.register("od.move", lambda b, a: {})


@pytest.mark.parametrize("legacy,normalized", [
    ("move", "od.move"),
    ("rename", "od.rename"),
    ("copy", "od.copy"),
    ("delete", "od.delete"),
    ("restore", "od.restore"),
    ("label-apply", "od.label-apply"),
    ("label-remove", "od.label-remove"),
    ("download", "od.download"),
    ("version-delete", "od.version-delete"),
    ("share-revoke", "od.share-revoke"),
    ("recycle-purge", "od.recycle-purge"),
    ("od.move", "od.move"),        # already namespaced — no-op
    ("mail.move", "mail.move"),     # mail verb — no-op
])
def test_normalize_legacy_action(legacy: str, normalized: str):
    assert normalize_legacy_action(legacy) == normalized
```

- [ ] **Step 2: Run to verify all fail**

Run: `uv run pytest tests/test_common_undo_dispatcher.py -q 2>&1 | tail -10`
Expected: all FAIL / ERROR (module doesn't exist yet).

---

### Task 18: `common/undo.Dispatcher` — implementation

**Files:**
- Create: `src/m365ctl/common/undo.py`

- [ ] **Step 1: Write the module**

Write `src/m365ctl/common/undo.py`:
```python
"""Domain-agnostic undo dispatcher for m365ctl.

Every reversible mutation registers an inverse builder keyed on
`<domain>.<verb>` (e.g. "od.move", "mail.flag"). Irreversible verbs
register a sentinel with an operator-facing explanation.

Legacy bare actions from pre-refactor audit entries (e.g. "move",
"rename") are normalized to their `od.*` equivalents on read so undo
still works on op-log lines written before Phase 0.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

InverseBuilder = Callable[[dict, dict], dict]
"""A function `(before, after) -> inverse_op_spec` where inverse_op_spec
is a dict with at minimum `{"action": "<domain>.<verb>", "args": {...}}`
suitable for re-feeding into the executor."""


_LEGACY_OD_ACTIONS = frozenset({
    "move", "rename", "copy", "delete", "restore",
    "label-apply", "label-remove", "download",
    "version-delete", "share-revoke", "recycle-purge",
})


class UnknownAction(KeyError):
    """Raised when asked to invert an action with no registered builder."""


class IrreversibleOp(RuntimeError):
    """Raised when the registered entry for an action is a sentinel."""


@dataclass(frozen=True)
class _Irreversible:
    reason: str


def normalize_legacy_action(action: str) -> str:
    """Prefix bare legacy OneDrive actions with `od.`; leave namespaced actions untouched."""
    if "." in action:
        return action
    if action in _LEGACY_OD_ACTIONS:
        return f"od.{action}"
    return action


class Dispatcher:
    """Registry mapping `<domain>.<verb>` to inverse builders."""

    def __init__(self) -> None:
        self._registry: dict[str, InverseBuilder | _Irreversible] = {}

    def register(self, action: str, builder: InverseBuilder) -> None:
        if action in self._registry:
            raise ValueError(f"action {action!r} already registered")
        self._registry[action] = builder

    def register_irreversible(self, action: str, reason: str) -> None:
        if action in self._registry:
            raise ValueError(f"action {action!r} already registered")
        self._registry[action] = _Irreversible(reason=reason)

    def build_inverse(self, action: str, *, before: dict, after: dict) -> dict:
        """Return an inverse op spec, or raise."""
        normalized = normalize_legacy_action(action)
        entry = self._registry.get(normalized)
        if entry is None:
            raise UnknownAction(f"no inverse builder registered for action {normalized!r}")
        if isinstance(entry, _Irreversible):
            raise IrreversibleOp(entry.reason)
        return entry(before, after)

    def is_registered(self, action: str) -> bool:
        return normalize_legacy_action(action) in self._registry

    def actions(self) -> list[str]:
        return sorted(self._registry)
```

- [ ] **Step 2: Run the new tests**

Run: `uv run pytest tests/test_common_undo_dispatcher.py -q`
Expected: all PASS.

- [ ] **Step 3: Run full suite**

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: baseline + 6 + 15 (5 dispatcher tests + 10 parametrized = 15; adjust if the test count differs).

- [ ] **Step 4: Commit**

```bash
git add src/m365ctl/common/undo.py tests/test_common_undo_dispatcher.py
git commit -m "feat(common): add domain-agnostic undo Dispatcher with legacy-action normalization"
```

---

### Task 19: Wire existing OneDrive inverses into the Dispatcher

**Files:**
- Modify: `src/m365ctl/onedrive/mutate/undo.py` (uses `new_op_id`; inverses return `Operation` objects)
- Modify: `src/m365ctl/onedrive/cli/undo.py` (replace hardcoded action routing with Dispatcher lookup)
- Modify: `src/m365ctl/cli/undo.py` (top-level delegate no longer needed — now real logic lives in common)

- [ ] **Step 1: Inspect existing inverse shapes**

Read `src/m365ctl/onedrive/mutate/undo.py` (149 lines). Each `inverse_<verb>` function returns an `Operation` dataclass (from `m365ctl.common.planfile`). The Dispatcher expects plain `dict` output. Two paths forward:
- (A) Wrap each inverse in a `(before, after) -> dict` adapter registered at import time.
- (B) Change the Dispatcher contract to accept `Operation` returns.

Choose (A) — keeps existing inverses untouched and matches the spec's "adapter to the Dispatcher" intent for mail inverses later.

- [ ] **Step 2: Append registration block to `common/undo.py`**

Create a module-level default Dispatcher **inside `src/m365ctl/onedrive/mutate/undo.py`** (not in `common/`, which must stay domain-agnostic):

Append to `src/m365ctl/onedrive/mutate/undo.py`:
```python
from m365ctl.common.undo import Dispatcher
from dataclasses import asdict as _asdict


def _as_dict(op) -> dict:
    """Convert a planfile.Operation into the dict shape the Dispatcher returns."""
    return _asdict(op)


def register_od_inverses(dispatcher: Dispatcher) -> None:
    """Register every OneDrive inverse builder on the supplied dispatcher."""
    dispatcher.register("od.rename",  lambda b, a: _as_dict(inverse_rename(b, a)))
    dispatcher.register("od.move",    lambda b, a: _as_dict(inverse_move(b, a)))
    dispatcher.register("od.delete",  lambda b, a: _as_dict(inverse_delete(b, a)))
    dispatcher.register("od.restore", lambda b, a: _as_dict(inverse_restore(b, a)))
    dispatcher.register("od.label-apply",  lambda b, a: _as_dict(inverse_label_apply(b, a)))
    dispatcher.register("od.label-remove", lambda b, a: _as_dict(inverse_label_remove(b, a)))
    # copy / download / version-delete / share-revoke / recycle-purge are
    # irreversible today; the existing CLI prints guidance rather than acting.
    dispatcher.register_irreversible("od.copy",           "Copy target lives only as a new item; delete the copy to 'undo'.")
    dispatcher.register_irreversible("od.download",       "Downloads are local-file artifacts; delete the file to 'undo'.")
    dispatcher.register_irreversible("od.version-delete", "Deleted file versions cannot be restored via Graph.")
    dispatcher.register_irreversible("od.share-revoke",   "Revoked sharing links cannot be restored; re-share explicitly.")
    dispatcher.register_irreversible("od.recycle-purge",  "Purged recycle-bin items are irrecoverable.")
```

(Names like `inverse_rename` must match the real function names in this file. If the file instead exposes `inverse_rename(before, after) -> Operation`, the adapter signature matches. If any inverse takes different arguments, adapt the lambda body.)

- [ ] **Step 3: Replace the CLI undo's action routing**

Open `src/m365ctl/onedrive/cli/undo.py`. The existing code (lines 62–85) manually dispatches on `rev.action in ("label-apply", "label-remove", "rename", "move", "delete", "restore")`. Wrap this in a call path that goes via the Dispatcher for new op-log lines but preserves the existing manual routing for the *executor* side (the Dispatcher builds the inverse spec; the executor still needs to carry it out).

Minimum-change approach: keep the existing manual executor; just add a preflight that uses `normalize_legacy_action` to normalize the stored `action` string, and surfaces `IrreversibleOp` / `UnknownAction` via a clean error message.

Insert at the top of the function that reads an op-log line:
```python
from m365ctl.common.undo import normalize_legacy_action, IrreversibleOp, UnknownAction
from m365ctl.onedrive.mutate.undo import register_od_inverses
from m365ctl.common.undo import Dispatcher

_DISPATCHER = Dispatcher()
register_od_inverses(_DISPATCHER)


# ... in the existing function, after reading `entry["action"]`:
action = normalize_legacy_action(entry["action"])
if not _DISPATCHER.is_registered(action):
    raise UnknownAction(f"no inverse for action {action!r}")
# The existing manual routing below then matches on the (normalized, stripped)
# action suffix; split on "." and use `.split(".", 1)[1]`.
```

Keep the executor's existing `if rev.action == "rename": ...` branches — just feed them the normalized action's suffix.

- [ ] **Step 4: Delete the shim in `src/m365ctl/cli/undo.py`**

Replace its contents with the real cross-domain delegate:
```python
"""m365ctl undo — cross-domain audit-log replay."""
from __future__ import annotations

from m365ctl.onedrive.cli.undo import main as _onedrive_undo_main


def main(argv: list[str] | None = None) -> int:
    # Phase 0: all registered inverses are `od.*`. Phase 1 wires `mail.*`.
    # The existing onedrive undo CLI already uses the Dispatcher-backed
    # lookup + legacy-action normalization — it is the cross-domain entry
    # point. When `mail.*` inverses land, a second `register_mail_inverses`
    # call joins them on the same Dispatcher singleton.
    return _onedrive_undo_main(argv) or 0
```

- [ ] **Step 5: Add a regression test for a legacy bare action**

Append to `tests/test_cli_undo.py` (or wherever the CLI undo is tested):
```python
def test_undo_tolerates_legacy_bare_action(tmp_path):
    from m365ctl.common.undo import normalize_legacy_action
    assert normalize_legacy_action("move") == "od.move"
    assert normalize_legacy_action("rename") == "od.rename"
```
(A full-fat integration test would require synthesizing a pre-refactor audit line; the normalization check is sufficient at the unit level — the rest is already covered by Task 17.)

- [ ] **Step 6: Run full suite**

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: baseline + 6 + 15 + 1.

- [ ] **Step 7: Commit**

```bash
git add src/m365ctl/onedrive/mutate/undo.py src/m365ctl/onedrive/cli/undo.py src/m365ctl/cli/undo.py tests/test_cli_undo.py
git commit -m "feat(undo): route OneDrive inverses through common.Dispatcher with legacy-action normalization"
```

---

### Task 20: Namespace plan-file actions (`od.*` prefix)

**Files:**
- Modify: `src/m365ctl/common/planfile.py`
- Modify: any call-site that emits an `Operation(action="move" | "rename" | …)` (inverses in Task 19, plan writers in `cli/_common.py` or `mutate/*.py`)

- [ ] **Step 1: Widen `_VALID_ACTIONS` to accept namespaced forms**

In `src/m365ctl/common/planfile.py`, replace the set:
```python
_VALID_ACTIONS: frozenset[str] = frozenset({
    # Current, namespaced.
    "od.move", "od.rename", "od.copy", "od.delete", "od.restore",
    "od.label-apply", "od.label-remove", "od.download",
    "od.version-delete", "od.share-revoke", "od.recycle-purge",
    # Legacy bare actions — accepted on read for pre-refactor plans; never emitted.
    "move", "rename", "copy", "delete", "restore",
    "label-apply", "label-remove", "download",
    "version-delete", "share-revoke", "recycle-purge",
})
```

- [ ] **Step 2: Rewrite `new_op_id` call sites to emit `od.*`**

Run: `grep -rn 'action="move"\|action="rename"\|action="copy"\|action="delete"\|action="restore"\|action="label-apply"\|action="label-remove"\|action="download"\|action="version-delete"\|action="share-revoke"\|action="recycle-purge"' src/ 2>/dev/null`
For each hit, prefix `od.` to the action literal.

Same for `action='move'` (single-quoted form):
Run: `grep -rn "action='move'\|action='rename'\|action='copy'\|action='delete'\|action='restore'\|action='label-apply'\|action='label-remove'\|action='download'\|action='version-delete'\|action='share-revoke'\|action='recycle-purge'" src/ 2>/dev/null`
Same treatment.

- [ ] **Step 3: Update tests that assert on the emitted action**

Run: `grep -rn 'action="move"\|action == "move"\|action == "rename"\|action=="move"' tests/ 2>/dev/null`
For each, decide per test:
- If the test is asserting the action EMITTED by a new mutation: assert `"od.move"`.
- If the test is feeding a hand-crafted legacy plan line: leave `"move"` to exercise the legacy-normalization path.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: baseline + 22. Any failure → inspect; likely an overlooked test assertion.

- [ ] **Step 5: Commit**

```bash
git add src/ tests/
git commit -m "refactor(planfile): namespace emitted actions with od.* prefix; accept legacy forms on read"
```

---

## Part D — Config extension for mail

### Task 21: `ScopeConfig`, `MailConfig`, `LoggingConfig` — failing tests

**Files:**
- Modify: `tests/test_config.py`

- [ ] **Step 1: Append failing tests for the new dataclass fields**

Append to `tests/test_config.py`:
```python
def test_config_loads_allow_mailboxes_and_deny_folders(tmp_path):
    from m365ctl.common.config import load_config
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("""
tenant_id    = "00000000-0000-0000-0000-000000000000"
client_id    = "11111111-1111-1111-1111-111111111111"
cert_path    = "~/.config/m365ctl/m365ctl.key"
cert_public  = "~/.config/m365ctl/m365ctl.cer"
default_auth = "delegated"

[scope]
allow_drives    = ["me"]
allow_mailboxes = ["me", "shared:ops@example.com"]
allow_users     = ["*"]
deny_paths      = ["/HR/**"]
deny_folders    = ["Archive/Legal/*"]
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
categories_master      = ["Followup", "Waiting"]
signature_path         = ""
drafts_before_send     = true
schedule_send_enabled  = false

[logging]
ops_dir        = "logs/ops"
purged_dir     = "logs/purged"
retention_days = 30
""".lstrip())
    cfg = load_config(cfg_path)
    assert cfg.scope.allow_mailboxes == ["me", "shared:ops@example.com"]
    assert cfg.scope.deny_folders == ["Archive/Legal/*"]
    assert cfg.mail.catalog_path.name == "mail.duckdb"
    assert cfg.mail.default_triage_root == "Inbox/Triage"
    assert cfg.mail.categories_master == ["Followup", "Waiting"]
    assert cfg.mail.signature_path is None          # empty string → None
    assert cfg.mail.drafts_before_send is True
    assert cfg.mail.schedule_send_enabled is False
    assert cfg.logging.purged_dir.name == "purged"
    assert cfg.logging.retention_days == 30


def test_config_mail_section_defaults_when_omitted(tmp_path):
    from m365ctl.common.config import load_config
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("""
tenant_id    = "00000000-0000-0000-0000-000000000000"
client_id    = "11111111-1111-1111-1111-111111111111"
cert_path    = "~/.config/m365ctl/m365ctl.key"
cert_public  = "~/.config/m365ctl/m365ctl.cer"
default_auth = "delegated"

[scope]
allow_drives = ["me"]

[catalog]
path = "cache/catalog.duckdb"

[logging]
ops_dir = "logs/ops"
""".lstrip())
    cfg = load_config(cfg_path)
    # scope defaults
    assert cfg.scope.allow_mailboxes == ["me"]
    assert cfg.scope.deny_folders == []
    # mail defaults (§7.2)
    assert cfg.mail.default_deleted_folder == "Deleted Items"
    assert cfg.mail.default_junk_folder == "Junk Email"
    assert cfg.mail.default_drafts_folder == "Drafts"
    assert cfg.mail.default_sent_folder == "Sent Items"
    assert cfg.mail.default_triage_root == "Inbox/Triage"
    assert cfg.mail.categories_master == []
    assert cfg.mail.signature_path is None
    assert cfg.mail.drafts_before_send is True
    assert cfg.mail.schedule_send_enabled is False
    # mail catalog_path default: "cache/mail.duckdb"
    assert cfg.mail.catalog_path.as_posix().endswith("cache/mail.duckdb")
    # logging defaults
    assert cfg.logging.purged_dir.as_posix().endswith("logs/purged")
    assert cfg.logging.retention_days == 30
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config.py -q 2>&1 | tail -10`
Expected: 2 new FAIL (old tests still pass).

---

### Task 22: `ScopeConfig`, `MailConfig`, `LoggingConfig` — implementation

**Files:**
- Modify: `src/m365ctl/common/config.py`

- [ ] **Step 1: Extend the dataclasses per spec §7.2**

Open `src/m365ctl/common/config.py`. Find the existing `ScopeConfig`, `CatalogConfig`, `LoggingConfig`, `Config` dataclasses. Add new fields and a new dataclass:

```python
from pathlib import Path
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScopeConfig:
    allow_drives: list[str]
    allow_mailboxes: list[str] = field(default_factory=lambda: ["me"])
    allow_users: list[str] = field(default_factory=lambda: ["*"])
    deny_paths: list[str] = field(default_factory=list)
    deny_folders: list[str] = field(default_factory=list)
    unsafe_requires_flag: bool = True


@dataclass(frozen=True)
class MailConfig:
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
    purged_dir: Path = field(default_factory=lambda: Path("logs/purged"))
    retention_days: int = 30


@dataclass(frozen=True)
class Config:
    tenant_id: str
    client_id: str
    cert_path: Path
    cert_public: Path
    default_auth: str  # AuthMode alias if defined
    scope: ScopeConfig
    catalog: CatalogConfig
    mail: MailConfig
    logging: LoggingConfig
```

- [ ] **Step 2: Teach `load_config` to parse the new sections**

In the same file, find `load_config(path: Path) -> Config`. Update to parse `[scope].allow_mailboxes`, `[scope].deny_folders`, the entire `[mail]` section, and `[logging].purged_dir` / `.retention_days`. Preserve existing behavior: keys omitted from TOML default to the dataclass defaults.

Key edge: `signature_path = ""` → `None` (empty string sentinel; otherwise resolve with `Path.expanduser()`).

Key edge: if `[mail]` section is absent entirely, construct `MailConfig(catalog_path=Path("cache/mail.duckdb"))` with all defaults.

- [ ] **Step 3: Run the new tests**

Run: `uv run pytest tests/test_config.py -q`
Expected: all PASS.

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: baseline + 24.

- [ ] **Step 4: Commit**

```bash
git add src/m365ctl/common/config.py tests/test_config.py
git commit -m "feat(config): add MailConfig, allow_mailboxes, deny_folders, purged_dir, retention_days (unused in Phase 0)"
```

---

### Task 23: Rewrite `config.toml.example` (generic placeholders)

**Files:**
- Overwrite: `config.toml.example`

- [ ] **Step 1: Write the spec §4.6 template verbatim**

Overwrite `config.toml.example` with:
```toml
# m365ctl - configuration template.
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
ops_dir        = "logs/ops"
purged_dir     = "logs/purged"                          # hard-delete EML captures
retention_days = 30
```

- [ ] **Step 2: Grep-verify no tenant-identifiable leaks**

Run: `grep -n '361efb70\|b22e6fd3\|Confidential\|HR\|fazla' config.toml.example`
Expected: empty.

- [ ] **Step 3: Confirm the example parses through `load_config`**

Run:
```bash
uv run python -c "
from pathlib import Path
from m365ctl.common.config import load_config
cfg = load_config(Path('config.toml.example'))
print('tenant_id:', cfg.tenant_id)
print('mail catalog:', cfg.mail.catalog_path)
print('logging retention:', cfg.logging.retention_days)
"
```
Expected: three lines including `tenant_id: 00000000-0000-0000-0000-000000000000`.

- [ ] **Step 4: Commit**

```bash
git add config.toml.example
git commit -m "docs(config): rewrite config.toml.example with placeholders and mail/logging sections"
```

---

## Part E — Publishing readiness

### Task 24: LICENSE

**Files:**
- Create: `LICENSE`

- [ ] **Step 1: Copy the Apache-2.0 text**

Fetch the canonical text from https://www.apache.org/licenses/LICENSE-2.0.txt (or use a local tool that ships the template). The file must begin `Apache License\nVersion 2.0, January 2004`. Replace the `[yyyy] [name of copyright owner]` tail with:
```
Copyright 2026 Arda Eren

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

- [ ] **Step 2: Commit**

```bash
git add LICENSE
git commit -m "chore: add Apache-2.0 LICENSE"
```

---

### Task 25: README.md rewrite

**Files:**
- Overwrite: `README.md`

- [ ] **Step 1: Write the full README per spec §18.2**

Structure:
1. H1 + one-paragraph description.
2. Status + Python + License badges (use shields.io URLs keyed on the yet-to-exist `aren13/m365ctl` repo — links will go live after Task 45).
3. Feature list (OneDrive + Mail).
4. Quickstart block (copy from spec §18.2 item 4, adapted).
5. Links to `docs/setup/azure-app-registration.md` and `docs/setup/first-run.md`.
6. Safety model summary (4 bullets: dry-run default, plan-file workflow, scope allow-lists, undo).
7. Command reference index (link to `docs/` pages — placeholders acceptable; actual per-command docs land in their phases).
8. Contributing link → `CONTRIBUTING.md`.
9. License link → `LICENSE`.
10. Disclaimer: "This is an independent open-source project. Not affiliated with Microsoft."

Do not include any tenant-specific example. No `fazla` anywhere.

- [ ] **Step 2: Grep-verify**

Run: `grep -n 'Fazla\|fazla\|361efb70\|b22e6fd3' README.md`
Expected: empty.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): rewrite as tenant-agnostic m365ctl quickstart"
```

---

### Task 26: CONTRIBUTING.md

**Files:**
- Create: `CONTRIBUTING.md`

- [ ] **Step 1: Write per spec §18.3**

Sections:
- Dev setup (`uv sync`, optional `pre-commit install` — note: no pre-commit config ships yet; mention as future).
- Test commands: `uv run pytest -m "not live"` (unit + mocked); live smoke gated by `M365CTL_LIVE_TESTS=1` and a real tenant.
- Code style: ruff for lint, mypy for types. One command each.
- Commit message conventions: `<type>(<scope>): <subject>` — existing git log already uses this pattern.
- PR checklist: tests green, §4.5 de-brand grep clean, new CLI verb has a docstring + example.

- [ ] **Step 2: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs: add CONTRIBUTING.md"
```

---

### Task 27: CHANGELOG.md (0.1.0 entry)

**Files:**
- Create: `CHANGELOG.md`

- [ ] **Step 1: Write per spec §18.6**

```markdown
# Changelog

All notable changes to m365ctl are documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: add CHANGELOG.md with 0.1.0 entry"
```

---

### Task 28: `scripts/setup/create-cert.sh`

**Files:**
- Create: `scripts/setup/create-cert.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# create-cert.sh - generate a self-signed cert for m365ctl app-only auth.
# Usage:
#   scripts/setup/create-cert.sh [CN]
# Default CN is "m365ctl".
#
# Output:
#   ~/.config/m365ctl/m365ctl.key   - PEM private key, mode 600
#   ~/.config/m365ctl/m365ctl.cer   - PEM public cert (upload this to Entra)
#
# Next steps printed after success.
set -euo pipefail

CN="${1:-m365ctl}"
OUTDIR="${HOME}/.config/m365ctl"
KEY="${OUTDIR}/m365ctl.key"
CER="${OUTDIR}/m365ctl.cer"

mkdir -p "${OUTDIR}"
chmod 700 "${OUTDIR}"

if [[ -e "${KEY}" || -e "${CER}" ]]; then
    echo "create-cert.sh: ${KEY} or ${CER} already exists; refusing to overwrite." >&2
    exit 1
fi

openssl req -x509 -newkey rsa:4096 -sha256 -days 730 -nodes \
    -keyout "${KEY}" -out "${CER}" \
    -subj "/CN=${CN}"
chmod 600 "${KEY}"
chmod 644 "${CER}"

THUMB=$(openssl x509 -in "${CER}" -fingerprint -noout -sha1 | sed 's/.*=//' | tr -d ':')

cat <<EOF

Cert generated.
  CN:         ${CN}
  Private key: ${KEY}
  Public cert: ${CER}
  SHA-1:       ${THUMB}

Next steps:
  1. Upload ${CER} to your Entra app registration (Certificates & secrets → Certificates).
  2. Copy the thumbprint above into any tooling that needs it (e.g. PnP.PowerShell).
  3. Update config.toml: cert_path and cert_public should match the paths above.
EOF
```

- [ ] **Step 2: Make executable**

Run: `chmod +x scripts/setup/create-cert.sh`

- [ ] **Step 3: Commit**

```bash
git add scripts/setup/create-cert.sh
git commit -m "chore(setup): add scripts/setup/create-cert.sh"
```

---

### Task 29: `docs/setup/` pages

**Files:**
- Create: `docs/setup/azure-app-registration.md`
- Create: `docs/setup/certificate-auth.md`
- Create: `docs/setup/first-run.md`
- Create: `docs/setup/migrating-from-fazla-od.md`

- [ ] **Step 1: azure-app-registration.md**

Text-first walkthrough (per spec §20 Q6 recommendation). Sections:
1. Create app registration (Entra → App registrations → New). Link to https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app.
2. Add API permissions. List both OneDrive (Files.ReadWrite.All, Sites.ReadWrite.All, User.Read) and Mail (Mail.ReadWrite, Mail.Send, MailboxSettings.ReadWrite) scopes, delegated + application.
3. Admin consent.
4. Capture Directory (tenant) ID + Application (client) ID → paste into `config.toml`.
5. Certificate upload (forward-reference `certificate-auth.md`).

- [ ] **Step 2: certificate-auth.md**

- How to run `scripts/setup/create-cert.sh <CN>`.
- How to upload the public cert to Entra.
- How to paste paths into `config.toml` (`cert_path`, `cert_public`).
- How to verify with `./bin/od-auth whoami`.

- [ ] **Step 3: first-run.md**

End-to-end from `git clone` to green `whoami`. Commands: clone → `uv sync` → `cp config.toml.example config.toml` → run `create-cert.sh` → paste paths/IDs → `./bin/od-auth login` → `./bin/od-auth whoami`. Target: ≤ 20 minutes.

- [ ] **Step 4: migrating-from-fazla-od.md**

One page for the single user migrating from the old tree:
1. `mv ~/.config/fazla-od ~/.config/m365ctl` (optional; auto-migrated on first run).
2. `security delete-generic-password -s FazlaODToolkit` (run twice — `DelegatedTokenCache` and `PfxPassword`). Next login recreates under `m365ctl:*`.
3. `sed -i '' 's|~/.config/fazla-od|~/.config/m365ctl|g' config.toml` for any hardcoded paths.
4. Legacy `logs/ops/*.jsonl` entries remain undoable (bare-action normalization).
5. `FAZLA_OD_LIVE_TESTS=1` → `M365CTL_LIVE_TESTS=1`.

- [ ] **Step 5: Commit**

```bash
git add docs/setup/
git commit -m "docs(setup): add azure-app-registration, certificate-auth, first-run, migrating-from-fazla-od"
```

---

### Task 30: CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the workflow per spec §18.5**

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
        python: ["3.11", "3.12", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v2
        with:
          python-version: ${{ matrix.python }}
      - run: uv sync --all-extras
      - run: uv run ruff check
      - run: uv run mypy src
      - run: uv run pytest -m "not live"
```

- [ ] **Step 2: Verify `mypy src` succeeds locally**

Run: `uv run mypy src 2>&1 | tail -10`
Expected: success OR a known set of errors. If new errors surface because of the rename, fix them before committing. (Mypy-strict not assumed; target whatever level the existing codebase uses.)

- [ ] **Step 3: Verify `ruff check` succeeds locally**

Run: `uv run ruff check 2>&1 | tail -5`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add ruff + mypy + pytest matrix (3.11/3.12/3.13 × ubuntu/macos)"
```

---

### Task 31: AGENTS.md rewrite (tenant-agnostic)

**Files:**
- Overwrite: `AGENTS.md`

- [ ] **Step 1: Rewrite the file**

Aim for a generic agents guide covering both domains:
- Overview: m365ctl is a dual-domain CLI (OneDrive + Mail) targeting Microsoft Graph.
- Where to start: read `docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md`, then the renamed parent spec (§4.6 reference), then CONTRIBUTING.
- Package layout: common/ + onedrive/ + mail/.
- Safety envelope: dry-run default, plan-file workflow, scope allow-lists, undo dispatcher.
- Key conventions: TDD, per-mutation `before`/`after` audit, namespaced actions.
- How to run live tests: set `M365CTL_LIVE_TESTS=1` and run `pytest -m live`.

No fazla references. No tenant-specific values.

- [ ] **Step 2: Grep-verify**

Run: `grep -n 'Fazla\|fazla' AGENTS.md`
Expected: empty.

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): rewrite generic dual-domain m365ctl guide"
```

---

## Part F — Bin wrappers + spec canonicalization

### Task 32: Bin wrapper rename + cross-domain undo binary

**Files:**
- Move: `bin/od-undo` → `bin/m365ctl-undo`
- Create: `bin/od-undo` (deprecated alias)
- Modify: every `bin/od-*` wrapper

- [ ] **Step 1: Rename**

Run: `git mv bin/od-undo bin/m365ctl-undo`

- [ ] **Step 2: Point `m365ctl-undo` at the top-level dispatcher**

Overwrite `bin/m365ctl-undo`:
```bash
#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec uv run --project "$REPO" python -m m365ctl undo "$@"
```
Run: `chmod +x bin/m365ctl-undo`.

- [ ] **Step 3: Add a deprecated `bin/od-undo` alias**

Write `bin/od-undo`:
```bash
#!/usr/bin/env bash
# Deprecated: use `m365ctl-undo` (short) or `m365ctl undo` (full).
echo "bin/od-undo: deprecated; prefer bin/m365ctl-undo (same behavior)." >&2
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec uv run --project "$REPO" python -m m365ctl undo "$@"
```
Run: `chmod +x bin/od-undo`.

- [ ] **Step 4: Rewrite the remaining `bin/od-*` wrappers to go through the top-level dispatcher**

For each of `bin/od-auth`, `bin/od-catalog-refresh`, `bin/od-catalog-status`, `bin/od-clean`, `bin/od-copy`, `bin/od-delete`, `bin/od-download`, `bin/od-inventory`, `bin/od-label`, `bin/od-move`, `bin/od-rename`, `bin/od-search`, `bin/od-audit-sharing`: change the exec line from
```bash
exec uv run --project "$REPO" python -m m365ctl.onedrive.cli <verb> "$@"
```
to
```bash
exec uv run --project "$REPO" python -m m365ctl od <verb> "$@"
```
The existing comment on `bin/od-auth` also mentions `fazla-od` in the comment header — update.

Sed-apply:
```bash
for f in bin/od-auth bin/od-catalog-refresh bin/od-catalog-status bin/od-clean bin/od-copy bin/od-delete bin/od-download bin/od-inventory bin/od-label bin/od-move bin/od-rename bin/od-search bin/od-audit-sharing; do
  verb="${f#bin/od-}"
  sed -i '' "s|python -m m365ctl.onedrive.cli ${verb}|python -m m365ctl od ${verb}|g" "$f"
done
```
(Linux: drop `''`.)

- [ ] **Step 5: Smoke one wrapper end-to-end**

Run: `./bin/od-auth --help 2>&1 | head -20`
Expected: the normal auth --help output (no traceback, no "module not found").

- [ ] **Step 6: Run full suite**

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: baseline + 24.

- [ ] **Step 7: Commit**

```bash
git add -A bin/
git commit -m "refactor(bin): retarget od-* wrappers at m365ctl dispatcher; add m365ctl-undo; deprecate od-undo alias"
```

---

### Task 33: Canonicalize parent spec + mail spec

**Files:**
- Move: `docs/superpowers/specs/2026-04-24-m365ctl-design.md` → `docs/superpowers/specs/2026-04-24-m365ctl-design.md`
- Move: `docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md` → `docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md`

- [ ] **Step 1: Rename the parent spec**

Run: `git mv docs/superpowers/specs/2026-04-24-m365ctl-design.md docs/superpowers/specs/2026-04-24-m365ctl-design.md`

- [ ] **Step 2: Sed-rewrite the parent spec's content to de-brand**

Run:
```bash
sed -i '' \
  -e 's|fazla_od|m365ctl|g' \
  -e 's|fazla-od|m365ctl|g' \
  -e 's|Fazla OneDrive Toolkit|m365ctl|g' \
  -e 's|Fazla M365 tenant|Microsoft 365 tenant|g' \
  -e 's|Fazla-OneDrive|m365ctl|g' \
  -e 's|361efb70-ca20-41ae-b204-9045df001350|00000000-0000-0000-0000-000000000000|g' \
  -e 's|b22e6fd3-4859-43ae-b997-997ad3aaf14b|11111111-1111-1111-1111-111111111111|g' \
  -e 's|C38CC9B49D5E4D326B4A79ECAF33CD65B008BCBF|<your-cert-thumbprint>|g' \
  -e 's|~/.config/fazla-od|~/.config/m365ctl|g' \
  docs/superpowers/specs/2026-04-24-m365ctl-design.md
```
(Linux: drop `''`.)

Then audit manually — re-read the file for residual branding one paragraph at a time and fix any prose-level references the sed missed.

- [ ] **Step 3: Move the mail spec into its canonical location**

Run: `git mv docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md`

- [ ] **Step 4: Update any cross-references**

Run: `grep -rln 'm365ctl-design\|FAZLA-MAIL-SPEC\|docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md' docs/ src/ tests/ scripts/ 2>/dev/null`

For each hit: point at the new filenames (`2026-04-24-m365ctl-design.md`, `2026-04-24-m365ctl-mail-module.md`). The mail spec's own first paragraph references docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md; fix those self-references to the canonical path.

- [ ] **Step 5: Commit**

```bash
git add -A docs/ docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md
git commit -m "docs(specs): canonicalize parent spec (m365ctl-design) and mail module spec; de-brand"
```

---

### Task 34: De-brand prior plans (UUIDs + fazla strings)

**Files:**
- Modify: `docs/superpowers/plans/2026-04-24-foundation-and-auth.md`
- Modify: `docs/superpowers/plans/2026-04-24-search-and-readonly-ops.md`
- Modify: `docs/superpowers/plans/2026-04-24-catalog.md`
- Modify: `docs/superpowers/plans/2026-04-24-mutations-and-safety.md`
- Modify: `docs/superpowers/plans/2026-04-24-recycle-bin-odfb-followup.md`

- [ ] **Step 1: Tree-rewrite UUIDs + fazla + thumbprint in plans**

```bash
sed -i '' \
  -e 's|fazla_od|m365ctl|g' \
  -e 's|fazla-od|m365ctl|g' \
  -e 's|Fazla OneDrive Toolkit|m365ctl|g' \
  -e 's|Fazla M365 tenant|Microsoft 365 tenant|g' \
  -e 's|361efb70-ca20-41ae-b204-9045df001350|00000000-0000-0000-0000-000000000000|g' \
  -e 's|b22e6fd3-4859-43ae-b997-997ad3aaf14b|11111111-1111-1111-1111-111111111111|g' \
  -e 's|C38CC9B49D5E4D326B4A79ECAF33CD65B008BCBF|<your-cert-thumbprint>|g' \
  -e 's|~/.config/fazla-od|~/.config/m365ctl|g' \
  -e 's|FazlaODToolkit|m365ctl|g' \
  docs/superpowers/plans/*.md
```

- [ ] **Step 2: Grep-verify**

Run: `grep -rn 'fazla\|Fazla\|361efb70\|b22e6fd3\|C38CC9B49D5E4D326B4A79ECAF33CD65B008BCBF' docs/superpowers/plans/ 2>/dev/null`
Expected: empty.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/
git commit -m "docs(plans): de-brand prior plans (strip UUIDs, thumbprint, fazla references)"
```

---

### Task 35: NEXT-SESSION.md update

**Files:**
- Modify: `NEXT-SESSION.md`

- [ ] **Step 1: Record Phase 0 completion**

Open `NEXT-SESSION.md`. Append (or replace, as the file dictates) a section:
```markdown
## Phase 0 complete — 2026-04-24

- Package renamed `fazla_od` → `m365ctl`.
- Restructured into `common/` + `onedrive/` + `mail/` sub-packages.
- Mail tree scaffold only (empty). Phase 1 adds readers.
- Undo dispatcher extracted to `m365ctl.common.undo.Dispatcher`.
- Config extended with `[mail]`, `allow_mailboxes`, `deny_folders`, `purged_dir`, `retention_days`.
- Apache-2.0 LICENSE, README, CONTRIBUTING, CHANGELOG, CI, setup docs shipped.
- All §4.5 grep assertions clean (see Task 46).

Next session: Phase 1 — mail readers (list, get, search, folders, categories, rules, settings, attachments). See `docs/superpowers/specs/2026-04-24-m365ctl-mail-module.md` §19 Phase 1. Author a plan via `superpowers:writing-plans` before executing.
```

Strip any still-relevant fazla references elsewhere in the file.

- [ ] **Step 2: Grep-verify**

Run: `grep -n 'Fazla\|fazla' NEXT-SESSION.md`
Expected: empty.

- [ ] **Step 3: Commit**

```bash
git add NEXT-SESSION.md
git commit -m "docs: mark Phase 0 complete in NEXT-SESSION.md"
```

---

## Acceptance gate

### Task 36: §4.5 grep suite — must all return empty (except documented exceptions)

**Files:** none (read-only verification)

- [ ] **Step 1: Run the full sweep**

```bash
grep -rni 'fazla'     src/ tests/ bin/ scripts/ docs/ pyproject.toml README.md AGENTS.md config.toml.example | \
  grep -v 'CHANGELOG.md' | \
  grep -v 'migrating-from-fazla-od.md'
grep -rni 'fazla_od'  src/ tests/ bin/ scripts/
grep -rni 'FazlaOD'   src/ tests/ bin/ scripts/
grep -rni 'FAZLA_OD'  src/ tests/ bin/ scripts/ | grep -v 'deprecation'  # allow the shim's reference
grep -rni '361efb70'  .
grep -rni 'b22e6fd3'  .
grep -rni 'C38CC9B49D5E4D326B4A79ECAF33CD65B008BCBF' .
```

Expected: all empty (or filtered to documented exceptions). If the `CHANGELOG.md` allow-list grep still shows mentions, confirm they're in the 0.1.0 entry documenting the rename — that's the only allowed place.

- [ ] **Step 2: If any leaks remain, fix them now**

Any output from Step 1 is a blocker. Fix and recommit — do not advance until clean.

---

### Task 37: Import + CLI smoke (hard gates)

**Files:** none

- [ ] **Step 1: Package import smoke**

Run:
```bash
uv run python -c "
import m365ctl
import m365ctl.common.auth
import m365ctl.common.graph
import m365ctl.common.config
import m365ctl.common.audit
import m365ctl.common.safety
import m365ctl.common.retry
import m365ctl.common.planfile
import m365ctl.common.undo
import m365ctl.onedrive.catalog
import m365ctl.onedrive.download
import m365ctl.onedrive.mutate
import m365ctl.onedrive.search
import m365ctl.onedrive.cli
import m365ctl.mail
import m365ctl.mail.catalog
import m365ctl.mail.mutate
import m365ctl.mail.triage
import m365ctl.mail.cli
import m365ctl.cli
print('import smoke: ok')
"
```
Expected: `import smoke: ok`.

- [ ] **Step 2: Top-level dispatcher smoke**

Run: `uv run python -m m365ctl --help 2>&1 | head -10`
Expected: the top-level usage banner from Task 6.

Run: `uv run python -m m365ctl od --help 2>&1 | head -20`
Expected: the OneDrive subcommand list.

Run: `uv run python -m m365ctl mail 2>&1 | head -5`
Expected: "mail domain is not yet implemented" (stderr) + exit 2.

- [ ] **Step 3: Full test matrix**

Run: `uv run pytest -m "not live" -q 2>&1 | tail -5`
Expected: green.

Run: `uv run ruff check 2>&1 | tail -5`
Expected: clean.

Run: `uv run mypy src 2>&1 | tail -5`
Expected: whatever baseline the repo accepted in Task 30 Step 2.

- [ ] **Step 4: Live-tenant smoke (user-performed)**

Instruct the user to run:
- `./bin/od-auth whoami` — must succeed against their real tenant.
- `./bin/od-inventory --top-by-size 10` — must print 10 rows.
- Pick one op_id from `logs/ops/*.jsonl` written before Phase 0 and run `./bin/m365ctl-undo <op-id>` (without `--confirm` first for dry-run). Must print a sensible reverse-op plan (exercising legacy-action normalization).

If any of these fail: the rename is not complete. Do not advance to Phase 1.

---

### Task 38: GitHub repo rename (user-performed, external)

**Files:** none (external action)

- [ ] **Step 1: Ask the user to rename the repo on GitHub**

Instruct the user:
1. GitHub → Settings → Repository name → `Fazla-OneDrive` → `m365ctl` → Rename.
2. Update local remote:
   ```bash
   git remote set-url origin git@github.com:<user>/m365ctl.git
   ```
3. Push the `phase-0-m365ctl-rebrand` branch:
   ```bash
   git push -u origin phase-0-m365ctl-rebrand
   ```
4. Open a PR for Phase 0 review, or merge directly if they're comfortable (user call).

- [ ] **Step 2: Update README badges**

Once the GitHub rename lands, update badge URLs in `README.md` to the new repo slug. Commit + push.

---

### Task 39: Branch merge & cleanup

**Files:** none

- [ ] **Step 1: Confirm green CI on the Phase 0 PR (if PR workflow)**

Wait for the GitHub Actions run triggered by Task 38 Step 1's push. All matrix cells must go green before merge.

- [ ] **Step 2: Merge to main**

User preference: squash-merge or merge-commit. Default: no-ff merge to preserve the task-by-task history that this plan was designed around.

- [ ] **Step 3: Clean up**

```bash
git checkout main
git pull
git branch -d phase-0-m365ctl-rebrand
```

---

## Self-Review Checklist

Before handing off for execution, I (the plan author) walked through:

**1. Spec coverage (Part A–E of §19 Phase 0):**
- Part A (Rename & restructure): Tasks 1–7 ✓
- Part B (De-branding): Tasks 8–16 ✓
- Part C (Publishing readiness): Tasks 24–31 ✓ (placed under "Part E" heading; order preserves coupling)
- Part D (Config extension): Tasks 21–23 ✓
- Part E (GitHub rename): Task 38 ✓
- Undo Dispatcher extraction (spec §5.3): Tasks 17–20 ✓
- Acceptance gates (§4.5 grep + import smoke + whoami smoke): Tasks 36–37 ✓

**2. Placeholder scan:**
- No "TBD", "implement later", "Add appropriate error handling" — every step has concrete content.
- Any step that changes code shows the full code block.
- Commands are exact (sed platform notes for macOS vs Linux called out).

**3. Type consistency:**
- `Dispatcher.register` → `(action: str, builder: InverseBuilder)` — consistent across Tasks 17, 18, 19.
- `normalize_legacy_action` — Task 18 defines, Task 19 consumes, Task 17 tests.
- `MailConfig` → spec §7.2 — Task 21 tests, Task 22 implements, Task 23 writes example TOML consuming it.
- Bin wrappers target `python -m m365ctl od <verb>` — Task 6 creates the router, Task 32 updates the wrappers.

**4. Risks explicitly documented in plan body:**
- Task 2 Step 5: restore-and-investigate on test regression.
- Task 8 Step 3: auto-migration OSError fallback to re-login.
- Task 19 Step 2: adapter path chosen over Dispatcher-contract change.
- Task 37 Step 4: user-performed live smoke is a hard gate — not optional.

---

Plan complete and saved to `docs/superpowers/plans/2026-04-24-phase-0-rename-restructure-publish.md`.
