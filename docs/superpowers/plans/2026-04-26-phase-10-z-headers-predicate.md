# Phase 10.z — Headers Predicate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Ship the `headers: { name: <s>, contains|equals|regex: <s> }` DSL predicate. Match against `internetMessageHeaders` for messages where any rule needs them. Per-message lazy fetch (only invoked when a `headers` predicate gates the decision); cached in `MatchContext` so the same message isn't fetched twice in the same triage run.

**Why lazy fetch (not catalog the headers):**
- Graph's list/delta endpoints don't reliably return `internetMessageHeaders`; the property is a heavyweight per-message resource.
- Cataloging headers would mean an extra GET per message at crawl time → doubles refresh wallclock for ~1% of users who'd ever match on headers.
- Lazy fetch at triage time has bounded cost: one GET per row that has a `headers` predicate in its rule's match. Cached per `(rule_set, message_id)` so multiple `headers` predicates on the same row only trigger one fetch.

**Approach:**
- New `HeadersP(name, contains, equals, regex)` dataclass + parser.
- `MatchContext` gains `header_fetcher: Callable[[str], list[dict]] | None` and a mutable `header_cache: dict[str, list[dict]]`. (Drops `frozen=True` from `MatchContext` — the cache mutates in place.)
- Match evaluator's `_eval_headers` reads cache; if miss + fetcher present, calls it once and stores. Returns False if no fetcher available (defensive).
- `runner.run_emit` builds the fetcher iff any rule in the loaded ruleset uses a `headers` predicate.

**Tech stack:** Existing primitives. No schema change. No crawl change. No new deps.

**Baseline:** `main` post-PR-#26 (03c587a), 928 passing tests, 0 mypy errors, ruff clean. Tag `v1.9.0`.

**Version bump:** 1.9.0 → 1.10.0.

---

## Group 1 — `HeadersP` + `MatchContext` extension + match evaluator (one commit)

**Files:**
- Modify: `src/m365ctl/mail/triage/dsl.py`
- Modify: `src/m365ctl/mail/triage/match.py`
- Modify: `tests/test_triage_dsl.py`
- Modify: `tests/test_triage_match.py`

### Steps

- [ ] **Step 1: Failing tests**

In `tests/test_triage_dsl.py`:
- DSL parses `headers: { name: List-Unsubscribe, contains: example.com }` → `HeadersP(name="List-Unsubscribe", contains="example.com")`.
- DSL parses `headers: { name: X-Spam-Status, equals: "Yes" }` → `HeadersP(name="X-Spam-Status", equals="Yes")`.
- DSL parses `headers: { name: Received, regex: "from .* by .*" }` → `HeadersP(name="Received", regex="from .* by .*")`.
- DSL parses `headers: { name: Foo }` → `HeadersP(name="Foo")` (existence-only).
- DSL rejects `headers: { contains: "x" }` (missing required `name`).
- DSL rejects `headers: { name: Foo, vibes: cool }` (unknown operator).

In `tests/test_triage_match.py`:
- `_eval_headers` matches when fetcher returns headers including `name=X` and `value` contains the substring (case-insensitive name match, value match per operator).
- `_eval_headers` returns False when fetcher returns no header with that name.
- `_eval_headers` returns False when no fetcher is configured (`header_fetcher=None`).
- `_eval_headers` caches: calling twice with the same message_id invokes fetcher only once.
- Multiple `HeadersP` predicates on the same row only fetch headers ONCE (cached across predicates).
- Existence-only (no contains/equals/regex) matches if the header is present at all.

- [ ] **Step 2:** Implement.

**`src/m365ctl/mail/triage/dsl.py`:**

```python
@dataclass(frozen=True)
class HeadersP:
    name: str                       # required, case-insensitive match against header name
    contains: str | None = None
    equals: str | None = None
    regex: str | None = None
```

Add to `Predicate` union. In `_parse_predicate`, replace any existing "headers not yet supported" stub with:

```python
if key == "headers":
    return _parse_headers_predicate(val, where=f"{where}.headers")
```

Add helper:

```python
def _parse_headers_predicate(val: Any, *, where: str) -> HeadersP:
    if not isinstance(val, dict):
        raise DslError(f"{where}: expected mapping with 'name' and an operator")
    known = {"name", "contains", "equals", "regex"}
    unknown = set(val.keys()) - known
    if unknown:
        raise DslError(f"{where}: unknown operator(s) {sorted(unknown)}")
    if "name" not in val:
        raise DslError(f"{where}: missing required 'name'")
    return HeadersP(
        name=val["name"],
        contains=val.get("contains"),
        equals=val.get("equals"),
        regex=val.get("regex"),
    )
```

**`src/m365ctl/mail/triage/match.py`:**

Drop `frozen=True` from `MatchContext` — the cache mutates in place:

```python
@dataclass
class MatchContext:
    """Pre-computed cross-row data + lazy fetcher caches."""
    replied_conversations: frozenset[str] = field(default_factory=frozenset)
    header_fetcher: Callable[[str], list[dict[str, str]]] | None = None
    header_cache: dict[str, list[dict[str, str]]] = field(default_factory=dict)
```

Imports needed: `from typing import Callable`. (Or `Callable` from `collections.abc`.)

Add `_eval_headers`:

```python
def _eval_headers(p: HeadersP, row: dict[str, Any], *, context: MatchContext) -> bool:
    headers = _get_headers_for(row, context)
    if headers is None:
        return False
    needle_name = p.name.lower()
    for h in headers:
        hname = (h.get("name") or "").lower()
        if hname != needle_name:
            continue
        value = h.get("value") or ""
        if p.equals is None and p.contains is None and p.regex is None:
            return True   # existence-only match
        if p.equals is not None and value == p.equals:
            return True
        if p.contains is not None and p.contains.lower() in value.lower():
            return True
        if p.regex is not None and re.search(p.regex, value):
            return True
    return False


def _get_headers_for(
    row: dict[str, Any], context: MatchContext,
) -> list[dict[str, str]] | None:
    """Return cached headers for this message, fetching once if needed."""
    msg_id = row.get("message_id")
    if not msg_id:
        return None
    if msg_id in context.header_cache:
        return context.header_cache[msg_id]
    if context.header_fetcher is None:
        return None
    headers = context.header_fetcher(msg_id)
    context.header_cache[msg_id] = headers
    return headers
```

Add dispatch in `_eval`:
```python
if isinstance(p, HeadersP):
    return _eval_headers(p, row, context=context)
```

- [ ] **Step 3:** Quality gates: pytest (928 + ~12 = ~940), mypy 0, ruff clean.

- [ ] **Step 4: Commit:**
```
git add src/m365ctl/mail/triage/dsl.py \
        src/m365ctl/mail/triage/match.py \
        tests/test_triage_dsl.py \
        tests/test_triage_match.py
git commit -m "feat(mail/triage): headers predicate with lazy per-message fetch + per-run cache"
```

---

## Group 2 — Runner wires the header fetcher (one commit)

**Files:**
- Modify: `src/m365ctl/mail/triage/runner.py`
- Modify: `tests/test_triage_runner.py` (or `tests/test_triage_plan.py` — wherever the orchestration tests live)

### Steps

- [ ] **Step 1:** Build a header fetcher closure in the runner. Only attach it to `MatchContext` if any rule has a `headers` predicate (cheap pre-scan keeps the contract clean — rulesets without headers don't pay any per-message-fetch cost).

In `src/m365ctl/mail/triage/runner.py`, find where `build_plan` is called by `run_emit` (the function that opens the catalog, queries rows, and emits the plan). The plan-emit currently builds a `MatchContext` via `_build_match_context(rows)` (Phase 10.y added this in `plan.py`).

Update the orchestration so the runner can supply a fetcher to `_build_match_context` AND `build_plan` can pass it through to `MatchContext`. Two clean shapes work:
1. **Add a `header_fetcher` kwarg to `build_plan`** — passes through to `MatchContext`.
2. **Build `MatchContext` in the runner**, pass it to `build_plan` directly.

Pick (1) — minimal API churn:

In `src/m365ctl/mail/triage/plan.py`:
```python
def build_plan(
    ruleset: RuleSet,
    rows: Iterable[dict[str, Any]],
    *,
    mailbox_upn: str,
    source_cmd: str,
    scope: str,
    now: datetime,
    header_fetcher: Callable[[str], list[dict[str, str]]] | None = None,
) -> Plan:
    rows_list = list(rows)
    context = _build_match_context(rows_list, header_fetcher=header_fetcher)
    ...

def _build_match_context(
    rows: list[dict[str, Any]],
    *,
    header_fetcher: Callable[[str], list[dict[str, str]]] | None = None,
) -> MatchContext:
    senders_by_conv: dict[str, set[str]] = {}
    for r in rows:
        ...
    return MatchContext(
        replied_conversations=frozenset(...),
        header_fetcher=header_fetcher,
    )
```

In `src/m365ctl/mail/triage/runner.py:run_emit`, after loading the ruleset and BEFORE calling `build_plan`:
```python
def run_emit(...):
    ruleset = ...
    needs_headers = _ruleset_needs_headers(ruleset)
    fetcher = _make_header_fetcher(graph=...) if needs_headers else None
    plan = build_plan(
        ruleset, rows,
        ...,
        header_fetcher=fetcher,
    )
```

But wait — `run_emit` doesn't currently take a `graph` arg (it's a catalog-only path). The fetcher needs Graph access. Need to thread Graph + auth_mode + mailbox_spec into `run_emit`, OR build the fetcher inside the CLI layer and pass into `run_emit`.

Cleaner: add `header_fetcher` as a kwarg to `run_emit`, defaulting to None. The CLI layer (`mail/cli/triage.py`) constructs the fetcher when needed.

Actually simpler: the CLI already has Graph. Let CLI decide. `run_emit` takes optional `header_fetcher`. The CLI wires it.

```python
# In runner.py:
def _ruleset_needs_headers(ruleset) -> bool:
    """Scan AST for any HeadersP predicate to decide if we need a fetcher."""
    from m365ctl.mail.triage.dsl import HeadersP
    for rule in ruleset.rules:
        for predicate_list in (rule.match.all_of, rule.match.any_of, rule.match.none_of):
            if any(isinstance(p, HeadersP) for p in predicate_list):
                return True
    return False


def run_emit(
    *,
    rules_path,
    catalog_path,
    mailbox_upn,
    scope,
    plan_out,
    header_fetcher: Callable[[str], list[dict[str, str]]] | None = None,
) -> Plan:
    ...
    plan = build_plan(
        ruleset, rows,
        mailbox_upn=mailbox_upn,
        source_cmd=...,
        scope=scope,
        now=...,
        header_fetcher=header_fetcher,
    )
    ...


def make_header_fetcher(
    graph, *, mailbox_spec: str, auth_mode: str,
) -> Callable[[str], list[dict[str, str]]]:
    """Build a closure that fetches a single message's internetMessageHeaders.
    
    Uses ?$select=internetMessageHeaders. Returns [] on error so a missing
    message doesn't crash the whole triage run.
    """
    from m365ctl.mail.endpoints import user_base
    from m365ctl.common.graph import GraphError
    ub = user_base(mailbox_spec, auth_mode=auth_mode)  # type: ignore[arg-type]
    def _fetch(message_id: str) -> list[dict[str, str]]:
        try:
            raw = graph.get(
                f"{ub}/messages/{message_id}",
                params={"$select": "internetMessageHeaders"},
            )
        except GraphError:
            return []
        return list(raw.get("internetMessageHeaders") or [])
    return _fetch
```

Then in the CLI `mail/cli/triage.py:_run_main` (or wherever `run_emit` is called), inspect the ruleset (or just always build the fetcher — cheap) and pass it through.

Actually the simplest: always build the fetcher (cheap closure), pass it to `run_emit`. The fetcher is only invoked if the cache miss + a `HeadersP` predicate fires. Zero overhead for rulesets without headers.

```python
# In mail/cli/triage.py:_run_main
if args.rules:
    ...
    graph = ...   # already constructed
    fetcher = make_header_fetcher(graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode)
    plan = run_emit(
        rules_path=Path(args.rules),
        catalog_path=cfg.mail.catalog_path,
        mailbox_upn=mailbox_upn,
        scope=mailbox_spec,
        plan_out=plan_out,
        header_fetcher=fetcher,
    )
```

- [ ] **Step 2: Tests** in `tests/test_triage_runner.py`:
  - `make_header_fetcher` returns a callable that calls `graph.get` with the right path and `$select=internetMessageHeaders`, returns the headers list.
  - The fetcher returns `[]` on `GraphError` (defensive — missing message shouldn't crash the run).
  - `run_emit` plumbs `header_fetcher` through to `build_plan` (mock `build_plan`, assert kwarg).
  - End-to-end: a ruleset with a `headers` predicate, mock fetcher returning a header that matches, assert the plan has the expected ops.

  Update existing `test_triage_plan.py` tests to assert `_build_match_context` accepts and stores `header_fetcher`.

- [ ] **Step 3:** Implement.

- [ ] **Step 4: Update CLI** `src/m365ctl/mail/cli/triage.py:_run_main`:
  - When in emit mode AND args.rules is set: build the fetcher via `make_header_fetcher(graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode)` AFTER constructing the GraphClient.
  - Pass it as `header_fetcher=fetcher` into `run_emit`.

- [ ] **Step 5: CLI tests** in `tests/test_cli_mail_triage.py`:
  - Existing tests should still pass (the fetcher is optional + defaults to None).
  - Add one test: `triage run --rules <yaml-with-headers-predicate> --plan-out` triggers `make_header_fetcher` (mock + assert called). Mock the GraphClient since CLI builds one for emit mode.

- [ ] **Step 6:** Quality gates: pytest (940 + ~6 = ~946), mypy 0, ruff clean.

- [ ] **Step 7: Commit:**
```
git add src/m365ctl/mail/triage/runner.py \
        src/m365ctl/mail/triage/plan.py \
        src/m365ctl/mail/cli/triage.py \
        tests/test_triage_runner.py \
        tests/test_triage_plan.py \
        tests/test_cli_mail_triage.py
git commit -m "feat(mail/triage): wire per-message header fetcher into runner + CLI"
```

---

## Group 3 — Release 1.10.0

### Task 3.1: Bump + changelog + README + lockfile (2 commits)

- [ ] `pyproject.toml`: 1.9.0 → 1.10.0.

- [ ] Prepend CHANGELOG.md:

```markdown
## 1.10.0 — Phase 10.z: headers predicate

### Added
- `headers: { name: <s>, contains|equals|regex: <s>? }` DSL predicate.
  Matches against `internetMessageHeaders`. The header `name` is matched
  case-insensitively; if no operator is given, the predicate is an
  existence check ("header is present"). Operators are evaluated against
  the header's `value` (case-sensitive `equals` and `regex`,
  case-insensitive `contains`).
- Lazy per-message fetch: a Graph GET with `?$select=internetMessageHeaders`
  is issued only when a `HeadersP` predicate gates the decision and the
  message's headers aren't already in the per-run cache. Multiple
  headers predicates on the same row share one fetch.
- `MatchContext` now non-frozen — carries the fetcher closure and an
  in-memory `header_cache` keyed by `message_id`. `header_fetcher`
  defaults to None; rulesets without `headers` predicates pay zero
  per-message cost.

### Why lazy fetch (not catalog the headers)
- `internetMessageHeaders` is a heavyweight per-message property.
  Capturing it at crawl time would double `mail catalog refresh`
  wallclock for the ~1% of users who'd ever match on headers.
- Lazy fetch at triage time has bounded cost (≤1 GET per row that
  appears in a header-using rule's candidate set).

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

### Status: spec parity 100%
This closes the last DSL deferral from Phase 10. The remaining backlog
items (Phase 4.x soft-delete-undo edge cases, Phase 7.x perf — already
shipped — and PyPI publish decision) are operational rather than
feature work.
```

- [ ] README Mail bullet:
```markdown
- **Headers predicate (Phase 10.z, 1.10):** `headers: { name: List-Unsubscribe, contains: example.com }`
  matches against `internetMessageHeaders` with lazy per-message fetch
  + per-run cache. Rulesets without headers predicates pay zero overhead.
```

- [ ] `uv sync --all-extras`. Quality gates. Two release commits per the no-amend rule.

### Task 3.2: Push, PR, merge, tag v1.10.0

Standard cadence.

---

## Self-review

**Spec coverage (Phase 10 deferrals from CHANGELOG 0.8.0):**
- ✅ `to`, `body`, `cc` predicates — shipped 1.6.0.
- ✅ `thread.has_reply` — shipped 1.7.0.
- ✅ `headers` — shipped here.
- ❌ KQL pushdown — explicitly deferred (purely an optimization; catalog covers our surface area).

**Backwards compat:**
- `MatchContext` lost `frozen=True` but kept all existing fields with defaults. Existing tests that construct `MatchContext()` or `MatchContext(replied_conversations=...)` continue to work.
- `build_plan`'s new `header_fetcher` kwarg defaults to None — existing callers (CLI's previous emit path) need no change beyond passing the new kwarg.
- `evaluate_match`'s signature unchanged.

**Type consistency:** `HeadersP` follows the existing `SubjectP`/`BodyP` shape (frozen dataclass, optional operators). `MatchContext` becomes mutable (the cache is dict-mutated); cleaner than wrestling frozen-dataclass workarounds.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-26-phase-10-z-headers-predicate.md`. Branch `phase-10-z-headers` already off `main`.
