# Phase 10.x — DSL Predicate Deferrals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Ship the three DSL predicates that Phase 10 deferred but the catalog can support (or can support after a small schema migration): `to`, `body`, `cc`. Acknowledge `thread` / `headers` / KQL pushdown as continuing deferrals.

**Scope decision:**
- `to` predicate: uses existing `mail_messages.to_addresses` column. Trivial.
- `body` predicate: uses existing `mail_messages.body_preview` column. Documented limitation — only matches the preview, not the full body. (Full-body matching would require either per-message Graph fetches at match time or a `body` column with `body.content` capture in the catalog crawl. Deferred.)
- `cc` predicate: requires a new `cc_addresses` column. Adds a non-destructive DuckDB `ALTER TABLE ADD COLUMN` migration; old rows have NULL until next refresh, new rows populate on the next `mail catalog refresh`.
- `thread`, `headers`, KQL pushdown: stay deferred. CHANGELOG calls out the rationale.

**Tech stack:** Existing DSL/match plumbing. Schema bump from v1 to v2 (DuckDB additive migration only).

**Baseline:** `main` post-PR-#22 (313fc9b), 880 passing tests, 0 mypy errors, ruff clean. Tag `v1.5.0`.

**Version bump:** 1.5.0 → 1.6.0.

---

## File Structure

**Modify:**
- `src/m365ctl/mail/triage/dsl.py` — add `ToP`, `CcP`, `BodyP` dataclasses; extend `_PREDICATE_KEYS`; add parser branches in `_parse_predicate`; allow `body` predicate (no longer rejected).
- `src/m365ctl/mail/triage/match.py` — add `_eval_to`, `_eval_cc`, `_eval_body` helpers + dispatch in `_eval`.
- `src/m365ctl/mail/catalog/schema.py` — bump `CURRENT_SCHEMA_VERSION` to 2; add `_DDL_V2` migration that does `ALTER TABLE mail_messages ADD COLUMN cc_addresses VARCHAR` (idempotent — wrap in `try` since DuckDB raises if column already exists).
- `src/m365ctl/mail/catalog/normalize.py` — `normalize_message` now reads `ccRecipients` from the Graph payload and joins to `cc_addresses`.
- `src/m365ctl/mail/catalog/crawl.py` — `_UPSERT_MESSAGE` includes the new `cc_addresses` column.
- `src/m365ctl/mail/triage/runner.py` — `_candidate_rows` SELECT adds `cc_addresses` (and `body_preview`, `to_addresses` already there).
- `pyproject.toml`, `CHANGELOG.md`, `README.md`.

**No CLI changes.** Predicates are DSL-only; users reference them via YAML.

---

## Group 1 — `to` and `body` predicates (one commit)

These two need no schema change. The catalog already has `to_addresses` (comma-joined) and `body_preview`.

### Steps

- [ ] **Step 1: Failing tests**

Extend `tests/test_triage_dsl.py` with:
- DSL parses `to: { domain_in: [example.com] }` to `ToP(domain_in=["example.com"])`.
- DSL parses `to: alice@example.com` (string shorthand) to `ToP(address="alice@example.com")`.
- DSL parses `body: { contains: "invoice" }` to `BodyP(contains="invoice")`.
- DSL parses `body: "invoice"` (string shorthand for contains) to `BodyP(contains="invoice")`.
- DSL still rejects unknown `body` operator (e.g. `body: { vibes: "good" }`).

Extend `tests/test_triage_match.py` with:
- `to` matches when `to_addresses` (comma-joined) contains the address.
- `to` with `domain_in` matches when any to-address has the listed domain.
- `body` matches when `body_preview` contains the substring (case-insensitive).
- `body` does NOT match when preview is empty / NULL.
- `body` regex / starts_with / ends_with all work over `body_preview`.

- [ ] **Step 2: Implement DSL changes** (`src/m365ctl/mail/triage/dsl.py`):

```python
@dataclass(frozen=True)
class ToP:
    address: str | None = None
    address_in: tuple[str, ...] | None = None
    domain_in: tuple[str, ...] | None = None

    def __init__(self, *, address=None, address_in=None, domain_in=None) -> None:
        object.__setattr__(self, "address", address)
        object.__setattr__(
            self, "address_in",
            tuple(address_in) if address_in is not None else None,
        )
        object.__setattr__(
            self, "domain_in",
            tuple(domain_in) if domain_in is not None else None,
        )


# BodyP shape mirrors SubjectP exactly — same operators.
@dataclass(frozen=True)
class BodyP:
    contains: str | None = None
    starts_with: str | None = None
    ends_with: str | None = None
    regex: str | None = None
    equals: str | None = None
```

Update `Predicate` union to include `ToP` / `BodyP`.

In `_parse_predicate`:
- `key == "to"` → `_parse_addr_predicate(ToP, val, where=...)` (same helper as `from`).
- `key == "body"` → replace the existing `raise DslError(...)` deferred-stub branch with `_parse_string_predicate(BodyP, val, where=...)`.
- Drop `body` from any deferred-rejection list (look for the existing `raise DslError(f"...predicate 'body' not supported in Phase 10...")` and remove it).

- [ ] **Step 3: Implement match changes** (`src/m365ctl/mail/triage/match.py`):

```python
def _eval_to(p: ToP, row: dict[str, Any]) -> bool:
    raw = (row.get("to_addresses") or "").lower()
    if not raw:
        return False
    addrs = [a.strip() for a in raw.split(",") if a.strip()]
    if p.address is not None and p.address.lower() not in addrs:
        return False
    if p.address_in is not None:
        wanted = {a.lower() for a in p.address_in}
        if not wanted.intersection(addrs):
            return False
    if p.domain_in is not None:
        domains = {a.split("@", 1)[-1] for a in addrs if "@" in a}
        wanted = {d.lower() for d in p.domain_in}
        if not wanted.intersection(domains):
            return False
    return True


def _eval_body(p: BodyP, row: dict[str, Any]) -> bool:
    s = row.get("body_preview") or ""
    if p.equals is not None and s != p.equals:
        return False
    if p.contains is not None and p.contains.lower() not in s.lower():
        return False
    if p.starts_with is not None and not s.startswith(p.starts_with):
        return False
    if p.ends_with is not None and not s.endswith(p.ends_with):
        return False
    if p.regex is not None and not re.search(p.regex, s):
        return False
    return True
```

Add dispatch in `_eval`:
- `if isinstance(p, ToP): return _eval_to(p, row)`
- `if isinstance(p, BodyP): return _eval_body(p, row)`

- [ ] **Step 4:** Run tests, mypy + ruff clean. Commit:
```
git add src/m365ctl/mail/triage/dsl.py src/m365ctl/mail/triage/match.py \
        tests/test_triage_dsl.py tests/test_triage_match.py
git commit -m "feat(mail/triage): to + body predicates over existing catalog columns"
```

---

## Group 2 — `cc_addresses` schema migration + `cc` predicate

The catalog needs a new column. DuckDB supports `ALTER TABLE ADD COLUMN`; the migration is non-destructive (existing rows get NULL).

### Task 2.1: Schema migration to v2 (one commit)

- [ ] **Step 1: Failing tests** in `tests/test_mail_catalog_schema.py`:
  - `apply_schema` against an existing v1 catalog adds the `cc_addresses` column (use a v1-shaped catalog seed: create the v1 DDL by hand on a fresh `:memory:` DB, insert one row, then call `apply_schema` and verify the new column exists and the old row's value is NULL).
  - Re-applying v2 is idempotent.
  - Fresh database gets v2 schema directly with `cc_addresses` already present.

- [ ] **Step 2: Implement** in `src/m365ctl/mail/catalog/schema.py`:

```python
CURRENT_SCHEMA_VERSION = 2

# Existing _DDL_V1 unchanged (full table CREATE statements).

_DDL_V2_MIGRATIONS = """
ALTER TABLE mail_messages ADD COLUMN IF NOT EXISTS cc_addresses VARCHAR;
"""


def apply_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(_DDL_V1)
    # Always run v2 migration after v1 — DuckDB's IF NOT EXISTS makes it
    # idempotent for fresh databases (already at the v2 shape because the
    # base CREATE includes the column once we update _DDL_V1) and for
    # existing v1 databases (adds the column).
    conn.execute(_DDL_V2_MIGRATIONS)
    row = conn.execute(
        "SELECT COUNT(*) FROM mail_schema_meta WHERE version = ?",
        [CURRENT_SCHEMA_VERSION],
    ).fetchone()
    assert row is not None
    (already,) = row
    if already == 0:
        conn.execute(
            "INSERT INTO mail_schema_meta (version) VALUES (?)",
            [CURRENT_SCHEMA_VERSION],
        )
```

**Also update `_DDL_V1`** so fresh databases get `cc_addresses` in the initial `CREATE TABLE mail_messages`. Keep the `_DDL_V2_MIGRATIONS` step for upgrading existing v1 catalogs. This dual-path is necessary because `IF NOT EXISTS` on `ADD COLUMN` makes the v2 step a no-op for fresh databases — but the column needs to exist regardless.

- [ ] **Step 3:** Update `src/m365ctl/mail/catalog/normalize.py`:
  - `normalize_message` (full-payload branch): add `"cc_addresses": _join_addrs(raw.get("ccRecipients"))`.
  - Tombstone branch: `"cc_addresses": None` (or `""` — match the existing `to_addresses` tombstone shape, currently `""` per `to_addresses` line).

- [ ] **Step 4:** Update `src/m365ctl/mail/catalog/crawl.py`:
  - `_UPSERT_MESSAGE` SQL: add `cc_addresses` to the column list, the `VALUES` placeholder list, and the `ON CONFLICT … DO UPDATE SET` clause.

- [ ] **Step 5:** Tests in `tests/test_mail_catalog_normalize.py`:
  - `normalize_message` with `ccRecipients` populates `cc_addresses` as comma-joined.
  - `normalize_message` with no `ccRecipients` writes `""` (matching `to_addresses` shape).
  - Tombstone: `cc_addresses` is the same null-ish value as `to_addresses` in the tombstone branch.

- [ ] **Step 6:** Quality gates. Commit:
```
git add src/m365ctl/mail/catalog/schema.py \
        src/m365ctl/mail/catalog/normalize.py \
        src/m365ctl/mail/catalog/crawl.py \
        tests/test_mail_catalog_schema.py \
        tests/test_mail_catalog_normalize.py
git commit -m "feat(mail/catalog): schema v2 — add cc_addresses (migration + crawl + normalize)"
```

### Task 2.2: `cc` predicate (one commit)

- [ ] **Step 1: Failing tests:**
  - DSL: `cc: { domain_in: [example.com] }` parses to `CcP(domain_in=["example.com"])`.
  - DSL: `cc: alice@example.com` parses to `CcP(address="alice@example.com")`.
  - Match: `cc` with `address_in` matches when any cc-address is in the list.
  - Match: `cc` with `domain_in` matches when any cc-address has the domain.
  - Match: `cc` against NULL `cc_addresses` (existing pre-migration row) returns False — does not crash.

- [ ] **Step 2: Implement:**

In `dsl.py`: add `CcP` (mirror `ToP` shape) + parser branch `key == "cc": return _parse_addr_predicate(CcP, val, where=...)`. Add to `Predicate` union.

In `match.py`: add `_eval_cc(p, row)` (mirror `_eval_to` but reading `cc_addresses`). Dispatch in `_eval`.

In `runner.py`: `_candidate_rows` SELECT — add `cc_addresses` to the column list so matched rows have the field.

- [ ] **Step 3:** Quality gates. Commit:
```
git add src/m365ctl/mail/triage/dsl.py \
        src/m365ctl/mail/triage/match.py \
        src/m365ctl/mail/triage/runner.py \
        tests/test_triage_dsl.py \
        tests/test_triage_match.py
git commit -m "feat(mail/triage): cc predicate (uses new catalog cc_addresses column)"
```

---

## Group 3 — Release 1.6.0

### Task 3.1: Bump + changelog + README + lockfile (2 commits)

- [ ] `pyproject.toml`: 1.5.0 → 1.6.0.

- [ ] Prepend CHANGELOG.md:

```markdown
## 1.6.0 — Phase 10.x: DSL predicate deferrals (to / body / cc)

### Added DSL predicates
- `to: { address | address_in | domain_in }` — uses existing
  `mail_messages.to_addresses` column. Composable in all/any/none.
- `body: { contains | starts_with | ends_with | regex | equals }` —
  matches against `mail_messages.body_preview`. **Limitation:** only
  the preview (first ~256 chars) is matched, not the full body. Full-body
  matching would require per-message Graph fetches at match time or a
  larger catalog footprint; deferred.
- `cc: { address | address_in | domain_in }` — uses the new
  `cc_addresses` column.

### Schema migration
- `mail_messages` schema bumped from v1 to v2: adds `cc_addresses
  VARCHAR` column. Migration is non-destructive (`ALTER TABLE … ADD
  COLUMN IF NOT EXISTS`); existing rows get NULL until the next
  `mail catalog refresh` repopulates them.

### Still deferred
- `thread.has_reply: false` — needs per-conversation walk; not in
  catalog.
- `headers: { contains | equals }` — needs `internetMessageHeaders`
  per-message fetch.
- KQL pushdown — local catalog covers the surface; pushdown is purely
  an optimization for cases the catalog can't handle.
```

- [ ] README Mail bullet:
```markdown
- **DSL predicates extended (Phase 10.x, 1.6):** triage rules now
  support `to`, `body`, `cc`. Catalog schema bumped to v2 (additive
  `cc_addresses` migration; existing catalogs auto-upgrade on next
  refresh).
```

- [ ] `uv sync --all-extras`. Quality gates. Two release commits per the no-amend rule.

### Task 3.2: Push, PR, merge, tag v1.6.0

Standard cadence.

---

## Self-review

**Spec coverage (Phase 10.x deferrals from CHANGELOG 0.8.0):**
- ✅ `to` predicate.
- ✅ `body` predicate (over preview only — documented).
- ✅ `cc` predicate (with schema migration).
- ⚠️ `thread`, `headers`, KQL pushdown — explicitly deferred, rationale in CHANGELOG.

**Schema migration safety:**
- `ALTER TABLE … ADD COLUMN IF NOT EXISTS` — DuckDB supports this and it's idempotent.
- Old rows get NULL → match logic must tolerate NULL (the `(row.get("cc_addresses") or "")` pattern handles it).
- `apply_schema` is run on every `open_catalog()` — every existing catalog auto-upgrades on next CLI invocation that touches it.
- Schema-meta v2 row is inserted only if not already present.

**Type consistency:** `ToP`/`CcP`/`BodyP` follow the existing `FromP`/`SubjectP` shape exactly. Match dispatch table extends cleanly.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-25-phase-10-x-dsl-predicates.md`. Branch `phase-10-x-dsl-predicates` already off `main`.
