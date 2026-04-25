# Phase 10.y — Thread Predicate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Ship `thread: { has_reply: false }` (and `has_reply: true`) — the predicate the spec's `follow-up-on-sent` rule example uses. Catalog already has `conversation_id` per message; the runner pre-computes which conversations have replies (≥2 distinct senders) and passes a `MatchContext` to the evaluator. No per-message Graph fetches; pure catalog reasoning.

**Definition:** A conversation "has a reply" if it has **≥ 2 distinct senders** (`from_address`). This matches the intuitive meaning ("someone replied to my outbound mail") without needing to know who "me" is — works for `me`, `upn:`, and `shared:` mailboxes uniformly.

**Tech stack:** Existing primitives. Schema unchanged.

**Baseline:** `main` post-PR-#23 (64c0471), 899 passing tests, 0 mypy errors, ruff clean. Tag `v1.6.0`.

**Version bump:** 1.6.0 → 1.7.0.

---

## Group 1 — `ThreadP` + `MatchContext` + runner wiring (one commit)

**Files:**
- Modify: `src/m365ctl/mail/triage/dsl.py` — add `ThreadP`, parser branch, drop the deferred-stub for `thread`.
- Modify: `src/m365ctl/mail/triage/match.py` — add `MatchContext`, extend `evaluate_match` to accept it, add `_eval_thread`.
- Modify: `src/m365ctl/mail/triage/plan.py` — `build_plan` precomputes `MatchContext` from the row iterable, passes through to `evaluate_match`.
- Modify: `src/m365ctl/mail/triage/runner.py` — `_candidate_rows` SELECT adds `conversation_id`.
- Modify: existing tests in `tests/test_triage_dsl.py`, `test_triage_match.py`, `test_triage_plan.py`, `test_triage_runner.py` to cover the new predicate.

### Steps

- [ ] **Step 1: Failing tests**

Extend `tests/test_triage_dsl.py`:
- DSL parses `thread: { has_reply: false }` → `ThreadP(has_reply=False)`.
- DSL parses `thread: { has_reply: true }` → `ThreadP(has_reply=True)`.
- DSL rejects `thread: { has_reply: "maybe" }` (must be bool).
- DSL rejects `thread: { vibes: cool }` (unknown operator).

Extend `tests/test_triage_match.py`:
- `_eval_thread` with `has_reply=False` returns True when conversation_id is NOT in `replied_conversations`.
- `_eval_thread` with `has_reply=False` returns False when conversation_id IS in `replied_conversations`.
- `_eval_thread` with `has_reply=True` returns True when in set.
- `_eval_thread` with empty `conversation_id` returns False (defensive).
- `evaluate_match(match, row)` (no context kwarg) defaults to empty MatchContext — thread predicates always evaluate as False (not crash).
- Combined match: `all_of=[FromP(domain_in=[example.com]), ThreadP(has_reply=False)]` works as expected.

Extend `tests/test_triage_plan.py`:
- `build_plan(ruleset, rows, ...)` precomputes `replied_conversations` from rows (a conversation_id with ≥2 distinct senders).
- A rule with `thread: { has_reply: false }` against a row whose conversation has only 1 sender → emits ops.
- Same row but conversation has 2 senders → no ops.

### Implementation

**`src/m365ctl/mail/triage/dsl.py`:**

```python
@dataclass(frozen=True)
class ThreadP:
    has_reply: bool
```

Add to `Predicate` union. In `_parse_predicate`:

```python
if key == "thread":
    return _parse_thread_predicate(val, where=f"{where}.thread")
# ... and DROP the existing "raise DslError(...)" branch that says
# "predicate 'thread' not yet supported".

def _parse_thread_predicate(val: Any, *, where: str) -> ThreadP:
    if not isinstance(val, dict):
        raise DslError(f"{where}: expected mapping with 'has_reply'")
    known = {"has_reply"}
    unknown = set(val.keys()) - known
    if unknown:
        raise DslError(f"{where}: unknown operator(s) {sorted(unknown)}")
    if "has_reply" not in val:
        raise DslError(f"{where}: missing required 'has_reply'")
    if not isinstance(val["has_reply"], bool):
        raise DslError(f"{where}.has_reply: must be true|false, got {val['has_reply']!r}")
    return ThreadP(has_reply=val["has_reply"])
```

**`src/m365ctl/mail/triage/match.py`:**

```python
@dataclass(frozen=True)
class MatchContext:
    """Pre-computed cross-row data needed by some predicates.

    Built once per ruleset run by the plan emitter. ``thread`` predicates
    consult ``replied_conversations`` to avoid per-evaluation rebuilds.
    """
    replied_conversations: frozenset[str] = frozenset()


def evaluate_match(
    match: Match,
    row: dict[str, Any],
    *,
    now: datetime,
    context: MatchContext | None = None,
) -> bool:
    ctx = context or MatchContext()
    if match.all_of and not all(_eval(p, row, now=now, context=ctx) for p in match.all_of):
        return False
    if match.any_of and not any(_eval(p, row, now=now, context=ctx) for p in match.any_of):
        return False
    if match.none_of and any(_eval(p, row, now=now, context=ctx) for p in match.none_of):
        return False
    return True


def _eval(p: Predicate, row: dict[str, Any], *, now: datetime, context: MatchContext) -> bool:
    # ... existing branches ...
    if isinstance(p, ThreadP):
        return _eval_thread(p, row, context=context)
    # ...


def _eval_thread(p: ThreadP, row: dict[str, Any], *, context: MatchContext) -> bool:
    cid = row.get("conversation_id") or ""
    if not cid:
        return False
    is_replied = cid in context.replied_conversations
    return is_replied is p.has_reply
```

Update all existing `_eval_*` helpers to take `*, now, context` (most ignore `context`).

**`src/m365ctl/mail/triage/plan.py`:**

```python
def build_plan(
    ruleset: RuleSet,
    rows: Iterable[dict[str, Any]],
    *,
    mailbox_upn: str,
    source_cmd: str,
    scope: str,
    now: datetime,
) -> Plan:
    rows_list = list(rows)
    context = _build_match_context(rows_list)
    ops: list[Operation] = []
    for rule in ruleset.rules:
        if not rule.enabled:
            continue
        for row in rows_list:
            if not evaluate_match(rule.match, row, now=now, context=context):
                continue
            for action in rule.actions:
                ops.append(_op_for(rule, action, row, mailbox_upn=mailbox_upn))
    return Plan(...)


def _build_match_context(rows: list[dict[str, Any]]) -> MatchContext:
    """Conversations with ≥2 distinct senders are 'replied'."""
    senders_by_conv: dict[str, set[str]] = {}
    for r in rows:
        cid = r.get("conversation_id")
        sender = (r.get("from_address") or "").lower()
        if cid and sender:
            senders_by_conv.setdefault(cid, set()).add(sender)
    return MatchContext(
        replied_conversations=frozenset(
            cid for cid, senders in senders_by_conv.items() if len(senders) > 1
        ),
    )
```

Add the `MatchContext` import at the top.

**`src/m365ctl/mail/triage/runner.py`:**

In `_candidate_rows`, add `conversation_id` to the SELECT column list:

```python
cur = conn.execute(
    """
    SELECT message_id, subject, from_address, from_name,
           to_addresses, cc_addresses, body_preview,
           parent_folder_path, received_at, is_read,
           flag_status, has_attachments, importance,
           categories, inference_class,
           conversation_id
    FROM mail_messages
    WHERE mailbox_upn = ? AND is_deleted = false
    """,
    [mailbox_upn],
)
```

- [ ] **Step 2:** Run, verify ImportError / new tests fail.

- [ ] **Step 3:** Implement.

- [ ] **Step 4:** Quality gates: pytest (899 + ~10 = ~909), mypy 0, ruff clean.

- [ ] **Step 5: Commit:**
```
git add src/m365ctl/mail/triage/dsl.py \
        src/m365ctl/mail/triage/match.py \
        src/m365ctl/mail/triage/plan.py \
        src/m365ctl/mail/triage/runner.py \
        tests/test_triage_dsl.py \
        tests/test_triage_match.py \
        tests/test_triage_plan.py
git commit -m "feat(mail/triage): thread.has_reply predicate via precomputed conversation context"
```

---

## Group 2 — Release 1.7.0

### Task 2.1: Bump + changelog + README + lockfile (2 commits)

- [ ] `pyproject.toml`: 1.6.0 → 1.7.0.

- [ ] Prepend CHANGELOG.md:

```markdown
## 1.7.0 — Phase 10.y: thread.has_reply predicate

### Added
- `thread: { has_reply: true|false }` DSL predicate. A conversation is
  considered "replied" iff there are ≥ 2 distinct senders in the same
  `conversation_id` across the candidate row set. No Graph fetches —
  pure catalog reasoning, computed once per `mail triage run` via a new
  `MatchContext` precomputation step in the plan emitter.
- The spec's `follow-up-on-sent` rule example now works as written:
  ```yaml
  - name: follow-up-on-sent
    match:
      all:
        - from: { domain_in: [yourdomain.com] }
        - thread: { has_reply: false }
        - age: { older_than_days: 3 }
    actions:
      - flag: { status: flagged, due_days: 2 }
  ```

### Internal
- `evaluate_match(...)` now accepts an optional `context: MatchContext`
  kwarg. Existing callers passing positional/keyword args without
  `context` continue to work; `thread` predicates against an empty
  context evaluate as False (defensive default).

### Still deferred
- `headers: { contains | equals }` — needs per-message
  `internetMessageHeaders` fetch.
- KQL pushdown — local catalog covers the surface; pushdown is purely
  an optimization for cases the catalog can't handle.
```

- [ ] README Mail bullet:
```markdown
- **Thread predicate (Phase 10.y, 1.7):** `thread: { has_reply: false }`
  catches sent mail with no reply yet. Pure catalog reasoning, no per-
  message Graph fetches.
```

- [ ] `uv sync --all-extras`. Quality gates. Two release commits.

### Task 2.2: Push, PR, merge, tag v1.7.0

Standard cadence.

---

## Self-review

**Spec coverage:**
- ✅ `thread: { has_reply: bool }` predicate — the only `thread` operator the spec example used.
- ⚠️ `headers` and KQL pushdown still deferred; rationale repeated in CHANGELOG.

**Backwards compat:**
- `evaluate_match` signature change: `context` is keyword-only with a default. Existing test bodies and any external callers (none) work unchanged.
- All existing tests in `test_triage_match.py` continue to pass with the default-empty context (no `thread` predicates in any pre-1.7 ruleset).

**Type consistency:** `MatchContext` is `@dataclass(frozen=True)`. `replied_conversations` is `frozenset[str]` so it's hashable + immutable + safe to share.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-26-phase-10-y-thread-predicate.md`. Branch `phase-10-y-thread-predicate` already off `main`.
