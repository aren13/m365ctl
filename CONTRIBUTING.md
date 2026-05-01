# Contributing to m365ctl

Thanks for helping out. m365ctl is a small project and the bar is
straightforward: tests green, lint clean, and no tenant-specific values in the
tree.

## Dev setup

```bash
uv sync --all-extras
```

That installs the project plus its dev extras (`pytest`, `ruff`, `mypy`).

Then activate the local pre-push hook (one-time per checkout — runs the same
`ruff` / `mypy` / `pytest` gates CI runs, before each `git push`):

```bash
./bin/install-hooks
```

The hook lives at `.githooks/pre-push`, is idempotent, and can be bypassed
for a single push with `git push --no-verify` — but the CI ruleset on `main`
enforces the same checks before merge, so bypassing locally only delays the
failure.

## Tests

Unit + mocked integration (default — no network, no tenant):

```bash
uv run pytest -m "not live"
```

Live smoke tests against a real tenant. Requires a working `config.toml` and
an opt-in env var:

```bash
M365CTL_LIVE_TESTS=1 uv run pytest -m live
```

## Code style

```bash
uv run ruff check    # lint
uv run mypy src      # types
```

Both are enforced in CI (see `.github/workflows/ci.yml`).

## Commit messages

Format: `<type>(<scope>): <subject>`.

Types: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`.

Scopes mirror the package sub-tree or surface touched: `common`, `onedrive`,
`mail`, `cli`, `config`, `audit`, `readme`, `agents`, etc.

Keep subjects under 72 characters. Use the body for the *why*, not the *what*.

Examples:

```
feat(onedrive): add --top-by-age to od-inventory
fix(common): restore before-block when item is already in recycle bin
docs(setup): clarify admin-consent step for non-admins
```

## Pull request checklist

- [ ] `uv run pytest -m "not live"` green.
- [ ] `uv run ruff check` clean.
- [ ] `uv run mypy src` — known baseline (see CI).
- [ ] New CLI verb has a module docstring + a `--help` example in its doc page.
- [ ] No tenant-specific values in tests or docs (real UUIDs, emails with real
      domains, real site URLs). Use `example.com` / `contoso.com`.
- [ ] `CHANGELOG.md` updated under `## [Unreleased]`.
