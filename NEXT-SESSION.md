# Resumption notes — Fazla OneDrive Toolkit

*Written 2026-04-24 after completing Plans 1–2 and Plan 3 Tasks 1–4. Remaining: Plan 3 Tasks 5–12, all of Plan 4.*

## Where to start

Open a fresh Claude Code session in this repo and say something like:

> Resume execution of `docs/superpowers/plans/2026-04-24-search-and-readonly-ops.md` from **Task 5** using subagent-driven development. After Plan 3 finishes, execute `docs/superpowers/plans/2026-04-24-mutations-and-safety.md` end-to-end.

## Current state (verify with `git log --oneline | head -10`)

- `main` is at `a1f373a` (pushed to `aren13/Fazla-OneDrive`).
- **74 unit tests + 1 live-skipped** all passing. Run `uv run pytest 2>&1 | tail -3` to confirm.
- Plan 1 (auth), Plan 2 (catalog), and Plan 3 Tasks 1–4 are committed. Plan 3 Tasks 5–12 and all of Plan 4 are still to do.
- User's OneDrive was crawled during Plan 2 smoke-test (4,929 live files, 18.47 GB). Catalog in `cache/catalog.duckdb`. If the catalog is stale, run `./bin/od-catalog-refresh --scope me` before Plan 3 Task 12's live smoke test.
- Entra tenant id + client id live in `config.toml` (gitignored). Cert at `~/.config/fazla-od/fazla-od.key`, expires 2028-04-22. Public cert uploaded to Entra. `od-auth whoami` should confirm both flows.

## What's next

Plan 3 remaining tasks, in order — see the plan file for full bodies:
- **Task 5** (lines 1214–1698): Search — Graph source, catalog source, merger.
- **Task 6** (1699–2072): `od-search` CLI + bin wrapper.
- **Task 7** (2073–2321): Download planner + shared plan-file schema (coordinate with Plan 4 Task 1).
- **Task 8** (2322–2915): Streaming fetcher + `od-download` CLI.
- **Task 9** (2916–3067): PEM→PFX helper + PnP.PowerShell setup docs.
- **Task 10** (3068–3416): `audit-sharing.ps1` + `od-audit-sharing` CLI wrapper.
- **Task 11** (3417–3496): Update `AGENTS.md` + full-suite sanity.
- **Task 12** (3497–end): End-to-end live smoke test (user-driven, needs browser login + live tenant).

Then `docs/superpowers/plans/2026-04-24-mutations-and-safety.md` — 13 tasks.

## Tier by risk (decided this session)

Full 3-stage review (implementer → spec review → code quality review):
- **Plan 3 Task 9** — PEM→PFX cert conversion + Keychain password storage (security sensitive).
- **Plan 4 Task 3** — Safety module: allow/deny list + `/dev/tty` confirm + `ScopeViolation`. Load-bearing.
- **Plan 4 Task 9** — Adversarial safety test suite.
- **Plan 4 Task 11** — `od-undo` reverse-op builder (correctness critical).

Implementer + quick diff check only (skip formal reviewers):
- Everything else. Plans are tightly specified with full code blocks; implementers consistently pasted them correctly.

## Gotchas discovered during execution (don't re-hit these)

1. **`retry.py` has an asymmetric contract.** `max_attempts <= 1` re-raises the underlying exception with its type/attrs intact; `max_attempts >= 2` wraps exhaustion in `RetryExhausted`. Documented in the `with_retry` docstring (commit `1d25dbf`). Plan 3 Task 1's 5th test relies on this — don't change it.

2. **`_enumerate_tenant` has a `_collect` fallback.** When `graph.get_paginated` returns an empty iterator (MagicMock default in tests, real empty collections in prod), `_collect` falls back to `graph.get(path).value`. The `except` was narrowed to `(TypeError, AttributeError)` so real Graph errors still propagate. Don't revert to broad `except Exception`.

3. **`prompts.confirm_or_abort` wraps `OSError` at the call site** (not only inside `_open_tty`) so tests that monkeypatch `_open_tty` to raise `OSError` get the expected `TTYUnavailable`.

4. **Pre-existing `test_resolve_scope_rejects_unknown_scheme`** now asserts `"bogus:Finance"` instead of `"site:Finance"` since `site:` is now a valid scheme.

5. **`/dev/tty` confirms cannot be bypassed by agents** — that's the whole point. When executing Plan 4's safety tests live, use `--yes` or drive the terminal interactively. The tests mock `confirm_or_abort` directly.

## The pre-flight for Plan 3 Task 12's live smoke test

Task 12 needs real Graph writes if search/download hits the tenant. Verify ahead of time:
- `./bin/od-auth whoami` returns both identities with no `(not available)` messages.
- If the cert expiry shown by `whoami` is under 60 days, rotate the cert before running the tenant smoke test.
- If PnP.PowerShell isn't installed yet (Task 9 ships that), Task 10 (`od-audit-sharing`) will fail — make sure Task 9 ran first.

## Test counts at each milestone

- After Plan 1: 14 passed + 1 skipped.
- After Plan 2: 52 passed + 1 skipped.
- After Plan 3 Task 1: 57.
- After Plan 3 Task 2: 63.
- After Plan 3 Task 3: 70.
- After Plan 3 Task 4: **74** ← we are here.
- Plan 3 complete (per the plan's own sanity step): 109 passed.
- Plan 4 complete: 126 passed.

If a future task's "expected N passed" doesn't match, something regressed — investigate before moving on.

## If things break

- Pytest failures on existing tests: the implementer or a linter probably touched a file out of scope. Check `git diff HEAD~1 HEAD` on the failing task's commit.
- `./bin/od-auth whoami` returns 401 for app-only: cert probably rotated or Entra app broken. Check `Certificates & secrets` in Entra.
- Device-code login fails with `AADSTS7000218`: Entra's "Allow public client flows" toggle got flipped off. Flip it back on in app Authentication blade.
- Catalog queries return zero results: the crawler ran but didn't persist. Inspect with `duckdb cache/catalog.duckdb` directly.
