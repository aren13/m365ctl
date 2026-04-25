# Phase 10 — Triage DSL + Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan group-by-group. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** YAML rules → match against the Phase-7 catalog → emit a tagged `Plan` → confirm-execute via existing Phase 3/4 mutate paths. Ship `mail triage validate` (no Graph calls; CI-friendly) and `mail triage run --rules <yaml> [--plan-out <p>]` plus `mail triage run --from-plan <p> --confirm`.

**Architecture:**
- `m365ctl.mail.triage.dsl` — PyYAML loader → strongly-typed `RuleSet` / `Rule` / `Match` / `Action` AST. Validation rejects unknown predicates/operators/actions with file/path-in-tree context.
- `m365ctl.mail.triage.match` — Predicate evaluator. Each predicate is a small frozen dataclass with `evaluate(msg_row: dict) -> bool`. `Match` composes predicates with `all_of` / `any_of` / `none_of` (default = `all_of`).
- `m365ctl.mail.triage.plan` — Walks `RuleSet × catalog rows`, emits `Operation` list tagged with `args.rule_name`. Multiple rules matching the same message stack ops in declaration order. The whole batch becomes one `Plan` with the existing `m365ctl.common.planfile` schema.
- `m365ctl.mail.triage.runner` — Orchestrator. Opens the catalog, fetches candidate rows, applies `RuleSet`, emits `Plan`. Execution path dispatches each `Operation` to the matching `mail.mutate.*` executor (move/copy/flag/read/focus/categorize/delete soft) with audit + undo intact.
- CLI: `m365ctl mail triage validate <file.yaml>` and `m365ctl mail triage run [--rules X | --from-plan Y --confirm]`. Bin wrapper `bin/mail-triage`.
- Examples: three generic YAML files in `scripts/mail/rules/` using `example.com` domains only.

**Tech stack:** PyYAML (new dep), Python 3.11+, existing `mail.catalog.*` (Phase 7), existing `mail.mutate.*` executors (Phase 3/4), existing `common.planfile` (`Plan`/`Operation`).

**Baseline:** `main` post-PR-#9 (49ea1af), 573 passing tests, 0 mypy errors. Three sibling PRs (Phase 4.x undo, Phase 7.x max-rounds, Phase 1.x InefficientFilter fallback) are in-flight in parallel — they touch different files; no overlap with this plan.

**Version bump:** 0.7.0 → 0.8.0.

---

## File Structure

**New:**
- `src/m365ctl/mail/triage/dsl.py` — `RuleSet`, `Rule`, `Match`, predicate dataclasses (`FromP`, `SubjectP`, `FolderP`, `AgeP`, `UnreadP`, `FlaggedP`, `HasAttachmentsP`, `CategoriesP`, `FocusP`, `ImportanceP`), action dataclasses (`MoveA`, `CopyA`, `DeleteA`, `FlagA`, `ReadA`, `FocusSetA`, `CategorizeA`), `load_ruleset_from_yaml(path) -> RuleSet`, `DslError`.
- `src/m365ctl/mail/triage/match.py` — `evaluate_match(match, row, *, now) -> bool`. Predicate dispatch table. `now` is injectable for deterministic tests.
- `src/m365ctl/mail/triage/plan.py` — `build_plan(ruleset, rows, *, mailbox_upn, source_cmd, scope, now) -> Plan`. Each emitted `Operation.args` carries `rule_name`.
- `src/m365ctl/mail/triage/runner.py` — `run_validate(path)`, `run_emit(rules, *, catalog_path, plan_out)`, `run_execute(plan, *, cfg, mailbox_spec, auth_mode, graph)`.
- `src/m365ctl/mail/cli/triage.py` — argparse entry point for `validate` / `run` subcommands.
- `bin/mail-triage` — exec wrapper.
- `scripts/mail/rules/triage.example.yaml` — kitchen-sink example showing every predicate/action.
- `scripts/mail/rules/archive-newsletters.yaml` — generic newsletter archiver.
- `scripts/mail/rules/daily-triage.yaml` — generic daily-triage routine.
- `tests/test_triage_dsl.py`
- `tests/test_triage_match.py`
- `tests/test_triage_plan.py`
- `tests/test_triage_runner.py`
- `tests/test_cli_mail_triage.py`

**Modify:**
- `pyproject.toml` — add `pyyaml>=6.0` to runtime deps. Bump `0.7.0` → `0.8.0`.
- `src/m365ctl/mail/cli/__main__.py` — route new `triage` verb + add to `_USAGE`.
- `CHANGELOG.md` — `0.8.0` section.
- `README.md` — short Mail bullet for `mail triage`.

---

## Group 1 — DSL types + YAML loader + `validate`

**Files:**
- Create: `src/m365ctl/mail/triage/dsl.py`
- Create: `tests/test_triage_dsl.py`
- Modify: `pyproject.toml` (add `pyyaml>=6.0`)

### Task 1.1: Add PyYAML dep + commit lockfile

- [ ] **Step 1:** Edit `pyproject.toml`. Replace
```toml
dependencies = [
    "cryptography>=42",
    "duckdb>=1.1",
    "httpx>=0.27",
    "msal>=1.28",
    "msal-extensions>=1.2",
]
```
with
```toml
dependencies = [
    "cryptography>=42",
    "duckdb>=1.1",
    "httpx>=0.27",
    "msal>=1.28",
    "msal-extensions>=1.2",
    "pyyaml>=6.0",
]
```

- [ ] **Step 2:** Run `uv sync` and verify the import works:
```bash
uv sync
uv run python -c "import yaml; print(yaml.__version__)"
```

- [ ] **Step 3:** Commit:
```bash
git add pyproject.toml uv.lock
git commit -m "deps: add pyyaml for Phase 10 triage DSL"
```

### Task 1.2: DSL types + parser (TDD)

- [ ] **Step 1: Write failing tests** (`tests/test_triage_dsl.py`)

```python
from __future__ import annotations

from pathlib import Path

import pytest

from m365ctl.mail.triage.dsl import (
    AgeP, CategorizeA, DslError, FlagA, FolderP, FromP, ImportanceP,
    Match, MoveA, Rule, RuleSet, SubjectP, UnreadP,
    load_ruleset_from_yaml,
)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "rules.yaml"
    p.write_text(body)
    return p


def test_load_minimal_ruleset(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: r1
    match:
      from: { domain_in: [example.com] }
    actions:
      - move: { to_folder: Archive }
""")
    rs = load_ruleset_from_yaml(p)
    assert rs.version == 1
    assert rs.mailbox == "me"
    assert len(rs.rules) == 1
    r = rs.rules[0]
    assert r.name == "r1"
    assert r.enabled is True   # default
    assert r.match.all_of == [FromP(domain_in=["example.com"])]
    assert r.actions == [MoveA(to_folder="Archive")]


def test_load_full_ruleset(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: archive-newsletters
    enabled: true
    match:
      all:
        - from: { domain_in: [example-newsletter.com, another-news.com] }
        - age: { older_than_days: 7 }
        - folder: Inbox
    actions:
      - categorize: { add: [Archived/Newsletter] }
      - move: { to_folder: Archive/Newsletters }

  - name: urgent-leadership
    match:
      all:
        - unread: true
        - folder: Inbox
        - from: { address_in: [alice@example.com] }
    actions:
      - categorize: { add: [Triage/Followup] }
      - flag: { status: flagged, due_days: 2 }
""")
    rs = load_ruleset_from_yaml(p)
    assert len(rs.rules) == 2
    r0 = rs.rules[0]
    assert r0.match.all_of == [
        FromP(domain_in=["example-newsletter.com", "another-news.com"]),
        AgeP(older_than_days=7),
        FolderP(equals="Inbox"),
    ]
    assert r0.actions == [
        CategorizeA(add=["Archived/Newsletter"]),
        MoveA(to_folder="Archive/Newsletters"),
    ]
    r1 = rs.rules[1]
    assert UnreadP(value=True) in r1.match.all_of
    assert FlagA(status="flagged", due_days=2) in r1.actions


def test_unknown_predicate_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: bad
    match: { astrology_sign: leo }
    actions: [{ move: { to_folder: X } }]
""")
    with pytest.raises(DslError, match="unknown predicate.*astrology_sign"):
        load_ruleset_from_yaml(p)


def test_unknown_action_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: bad
    match: { unread: true }
    actions: [{ teleport: { to: mars } }]
""")
    with pytest.raises(DslError, match="unknown action.*teleport"):
        load_ruleset_from_yaml(p)


def test_unknown_predicate_operator_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: bad
    match: { from: { vibes: positive } }
    actions: [{ move: { to_folder: X } }]
""")
    with pytest.raises(DslError, match="unknown operator.*vibes.*from"):
        load_ruleset_from_yaml(p)


def test_disabled_rule_preserved(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: paused
    enabled: false
    match: { unread: true }
    actions: [{ move: { to_folder: X } }]
""")
    rs = load_ruleset_from_yaml(p)
    assert rs.rules[0].enabled is False


def test_version_must_be_1(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 99
mailbox: me
rules: []
""")
    with pytest.raises(DslError, match="unsupported.*version.*99"):
        load_ruleset_from_yaml(p)


def test_missing_required_top_level_field(tmp_path: Path) -> None:
    p = _write(tmp_path, "rules: []\n")
    with pytest.raises(DslError, match="missing.*version"):
        load_ruleset_from_yaml(p)


def test_match_any_of_and_none_of(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: complex
    match:
      any:
        - from: { domain_in: [a.com] }
        - subject: { contains: invoice }
      none:
        - folder: Spam
    actions: [{ read: false }]
""")
    rs = load_ruleset_from_yaml(p)
    m = rs.rules[0].match
    assert m.any_of == [
        FromP(domain_in=["a.com"]),
        SubjectP(contains="invoice"),
    ]
    assert m.none_of == [FolderP(equals="Spam")]


def test_top_level_predicate_shorthand(tmp_path: Path) -> None:
    """A bare predicate at match-level becomes a single-element all_of."""
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: r
    match: { unread: true }
    actions: [{ flag: { status: flagged } }]
""")
    rs = load_ruleset_from_yaml(p)
    assert rs.rules[0].match.all_of == [UnreadP(value=True)]
    assert rs.rules[0].match.any_of == []


def test_age_newer_than_days(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: recent
    match: { age: { newer_than_days: 1 } }
    actions: [{ read: true }]
""")
    rs = load_ruleset_from_yaml(p)
    assert rs.rules[0].match.all_of == [AgeP(newer_than_days=1)]


def test_importance_predicate(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: imp
    match: { importance: high }
    actions: [{ flag: { status: flagged } }]
""")
    rs = load_ruleset_from_yaml(p)
    assert rs.rules[0].match.all_of == [ImportanceP(equals="high")]
```

- [ ] **Step 2: Run, verify ImportError.**

- [ ] **Step 3: Implement** (`src/m365ctl/mail/triage/dsl.py`)

```python
"""Triage DSL — YAML loader → typed RuleSet AST.

Schema (version 1):

    version: 1
    mailbox: me | upn:<addr> | shared:<addr>
    rules:
      - name: <slug>
        enabled: true             # default true
        match: <Match>
        actions: [<Action>, ...]

`Match` is one of:
  - a single predicate (shorthand for `all: [<predicate>]`)
  - `{all: [<predicate>, ...], any: [...], none: [...]}` (any combination,
    each defaulting to []).

Predicates:
  - from / to / cc:  {address: <s>, address_in: [<s>...], domain_in: [<s>...]}
  - subject / body:  {contains, starts_with, ends_with, regex, equals: <s>}
  - folder:          {equals: <path>, in: [<path>...]} or shorthand `<path>`
  - age:             {older_than_days: N, newer_than_days: N}
  - unread:          true|false (= isRead inverted)
  - is_flagged:      true|false
  - has_attachments: true|false
  - categories:      {contains: <s>, in: [<s>...], equals: <s>}
  - focus:           focused|other
  - importance:      low|normal|high

Actions:
  - move:       {to_folder: <path>}
  - copy:       {to_folder: <path>}
  - delete:     {} (soft delete -> Deleted Items)
  - flag:       {status: notFlagged|flagged|complete, due_days: N?}
  - read:       true|false
  - focus:      focused|other
  - categorize: {add: [<s>...] | remove: [<s>...] | set: [<s>...]}

Unknown keys, predicates, operators, or actions raise DslError with a
human-readable path into the YAML tree.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml


CURRENT_DSL_VERSION = 1


class DslError(ValueError):
    """Raised on malformed triage YAML."""


# ----- Predicates -----

@dataclass(frozen=True)
class FromP:
    address: str | None = None
    address_in: tuple[str, ...] | None = None
    domain_in: tuple[str, ...] | None = None

    def __init__(
        self,
        *,
        address: str | None = None,
        address_in: list[str] | tuple[str, ...] | None = None,
        domain_in: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        # Frozen dataclass with normalized tuple fields for equality.
        object.__setattr__(self, "address", address)
        object.__setattr__(
            self, "address_in",
            tuple(address_in) if address_in is not None else None,
        )
        object.__setattr__(
            self, "domain_in",
            tuple(domain_in) if domain_in is not None else None,
        )


@dataclass(frozen=True)
class SubjectP:
    contains: str | None = None
    starts_with: str | None = None
    ends_with: str | None = None
    regex: str | None = None
    equals: str | None = None


@dataclass(frozen=True)
class FolderP:
    equals: str | None = None
    in_: tuple[str, ...] | None = None

    def __init__(
        self,
        *,
        equals: str | None = None,
        in_: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        object.__setattr__(self, "equals", equals)
        object.__setattr__(
            self, "in_",
            tuple(in_) if in_ is not None else None,
        )


@dataclass(frozen=True)
class AgeP:
    older_than_days: int | None = None
    newer_than_days: int | None = None


@dataclass(frozen=True)
class UnreadP:
    value: bool


@dataclass(frozen=True)
class FlaggedP:
    value: bool


@dataclass(frozen=True)
class HasAttachmentsP:
    value: bool


@dataclass(frozen=True)
class CategoriesP:
    contains: str | None = None
    equals: str | None = None
    in_: tuple[str, ...] | None = None

    def __init__(
        self,
        *,
        contains: str | None = None,
        equals: str | None = None,
        in_: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        object.__setattr__(self, "contains", contains)
        object.__setattr__(self, "equals", equals)
        object.__setattr__(
            self, "in_",
            tuple(in_) if in_ is not None else None,
        )


@dataclass(frozen=True)
class FocusP:
    equals: Literal["focused", "other"]


@dataclass(frozen=True)
class ImportanceP:
    equals: Literal["low", "normal", "high"]


Predicate = (
    FromP | SubjectP | FolderP | AgeP | UnreadP | FlaggedP
    | HasAttachmentsP | CategoriesP | FocusP | ImportanceP
)


# ----- Match composer -----

@dataclass(frozen=True)
class Match:
    all_of: list[Predicate] = field(default_factory=list)
    any_of: list[Predicate] = field(default_factory=list)
    none_of: list[Predicate] = field(default_factory=list)


# ----- Actions -----

@dataclass(frozen=True)
class MoveA:
    to_folder: str


@dataclass(frozen=True)
class CopyA:
    to_folder: str


@dataclass(frozen=True)
class DeleteA:
    pass


@dataclass(frozen=True)
class FlagA:
    status: Literal["notFlagged", "flagged", "complete"]
    due_days: int | None = None


@dataclass(frozen=True)
class ReadA:
    value: bool


@dataclass(frozen=True)
class FocusSetA:
    value: Literal["focused", "other"]


@dataclass(frozen=True)
class CategorizeA:
    add: tuple[str, ...] | None = None
    remove: tuple[str, ...] | None = None
    set_: tuple[str, ...] | None = None

    def __init__(
        self,
        *,
        add: list[str] | tuple[str, ...] | None = None,
        remove: list[str] | tuple[str, ...] | None = None,
        set_: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        object.__setattr__(self, "add", tuple(add) if add is not None else None)
        object.__setattr__(self, "remove", tuple(remove) if remove is not None else None)
        object.__setattr__(self, "set_", tuple(set_) if set_ is not None else None)


Action = MoveA | CopyA | DeleteA | FlagA | ReadA | FocusSetA | CategorizeA


# ----- Top level -----

@dataclass(frozen=True)
class Rule:
    name: str
    enabled: bool
    match: Match
    actions: list[Action]


@dataclass(frozen=True)
class RuleSet:
    version: int
    mailbox: str
    rules: list[Rule]


# ----- Parser -----

_PREDICATE_KEYS = {
    "from", "to", "cc", "subject", "body", "folder", "age",
    "unread", "is_flagged", "has_attachments", "categories",
    "focus", "importance",
}
_ACTION_KEYS = {
    "move", "copy", "delete", "flag", "read", "focus", "categorize",
}


def load_ruleset_from_yaml(path: Path | str) -> RuleSet:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise DslError(f"top-level YAML must be a mapping, got {type(raw).__name__}")
    return _parse_ruleset(raw, where="<root>")


def _parse_ruleset(raw: dict, *, where: str) -> RuleSet:
    if "version" not in raw:
        raise DslError(f"{where}: missing required field 'version'")
    if raw["version"] != CURRENT_DSL_VERSION:
        raise DslError(
            f"{where}: unsupported DSL version {raw['version']!r} "
            f"(expected {CURRENT_DSL_VERSION})"
        )
    if "mailbox" not in raw:
        raise DslError(f"{where}: missing required field 'mailbox'")
    rules_raw = raw.get("rules") or []
    if not isinstance(rules_raw, list):
        raise DslError(f"{where}.rules: must be a list")
    rules = [
        _parse_rule(r, where=f"{where}.rules[{i}]")
        for i, r in enumerate(rules_raw)
    ]
    return RuleSet(
        version=raw["version"],
        mailbox=str(raw["mailbox"]),
        rules=rules,
    )


def _parse_rule(raw: dict, *, where: str) -> Rule:
    if not isinstance(raw, dict):
        raise DslError(f"{where}: rule must be a mapping")
    if "name" not in raw:
        raise DslError(f"{where}: missing 'name'")
    match = _parse_match(raw.get("match") or {}, where=f"{where}.match")
    actions_raw = raw.get("actions") or []
    if not isinstance(actions_raw, list) or not actions_raw:
        raise DslError(f"{where}.actions: must be a non-empty list")
    actions = [
        _parse_action(a, where=f"{where}.actions[{i}]")
        for i, a in enumerate(actions_raw)
    ]
    return Rule(
        name=str(raw["name"]),
        enabled=bool(raw.get("enabled", True)),
        match=match,
        actions=actions,
    )


def _parse_match(raw: Any, *, where: str) -> Match:
    if not isinstance(raw, dict):
        raise DslError(f"{where}: must be a mapping")
    if not raw:
        return Match()
    keys = set(raw.keys())
    if keys & {"all", "any", "none"}:
        unknown = keys - {"all", "any", "none"}
        if unknown:
            raise DslError(
                f"{where}: cannot mix 'all'/'any'/'none' with bare predicate "
                f"keys; got extra keys {sorted(unknown)}"
            )
        return Match(
            all_of=_parse_predicate_list(raw.get("all") or [], where=f"{where}.all"),
            any_of=_parse_predicate_list(raw.get("any") or [], where=f"{where}.any"),
            none_of=_parse_predicate_list(raw.get("none") or [], where=f"{where}.none"),
        )
    # Shorthand: a single bare predicate (or several keys → all_of of each).
    return Match(all_of=[_parse_predicate(k, v, where=where) for k, v in raw.items()])


def _parse_predicate_list(raw: list, *, where: str) -> list[Predicate]:
    if not isinstance(raw, list):
        raise DslError(f"{where}: must be a list of predicates")
    out: list[Predicate] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or len(item) != 1:
            raise DslError(
                f"{where}[{i}]: each entry must be a single-key mapping "
                f"{{<predicate>: <args>}}"
            )
        (k, v), = item.items()
        out.append(_parse_predicate(k, v, where=f"{where}[{i}]"))
    return out


def _parse_predicate(key: str, val: Any, *, where: str) -> Predicate:
    if key not in _PREDICATE_KEYS:
        raise DslError(
            f"{where}: unknown predicate {key!r} (known: {sorted(_PREDICATE_KEYS)})"
        )
    if key == "from":
        return _parse_addr_predicate(FromP, val, where=f"{where}.from")
    if key == "to" or key == "cc":
        # First-cut: 'to' / 'cc' use the same shape as 'from'. Defer wiring
        # in match.py if/when there's a use case beyond `from` for triage.
        # For now reject with an explanatory error (so YAML doesn't silently
        # parse to a no-op).
        raise DslError(
            f"{where}: predicate {key!r} not yet supported (Phase 10.x); "
            f"use 'from' or open an issue."
        )
    if key == "subject":
        return _parse_string_predicate(SubjectP, val, where=f"{where}.subject")
    if key == "body":
        raise DslError(
            f"{where}: predicate 'body' not supported in Phase 10 — body "
            f"isn't in the catalog yet (Phase 10.x)."
        )
    if key == "folder":
        return _parse_folder_predicate(val, where=f"{where}.folder")
    if key == "age":
        return _parse_age_predicate(val, where=f"{where}.age")
    if key == "unread":
        if not isinstance(val, bool):
            raise DslError(f"{where}.unread: must be true|false, got {val!r}")
        return UnreadP(value=val)
    if key == "is_flagged":
        if not isinstance(val, bool):
            raise DslError(f"{where}.is_flagged: must be true|false, got {val!r}")
        return FlaggedP(value=val)
    if key == "has_attachments":
        if not isinstance(val, bool):
            raise DslError(f"{where}.has_attachments: must be true|false, got {val!r}")
        return HasAttachmentsP(value=val)
    if key == "categories":
        return _parse_categories_predicate(val, where=f"{where}.categories")
    if key == "focus":
        if val not in ("focused", "other"):
            raise DslError(f"{where}.focus: must be 'focused' or 'other', got {val!r}")
        return FocusP(equals=val)
    if key == "importance":
        if val not in ("low", "normal", "high"):
            raise DslError(
                f"{where}.importance: must be 'low'/'normal'/'high', got {val!r}"
            )
        return ImportanceP(equals=val)
    raise DslError(f"{where}: unhandled predicate {key!r}")  # pragma: no cover


def _parse_addr_predicate(cls, val: Any, *, where: str):
    # Allow shorthand string ("me" or "alice@example.com") meaning {address: <s>}.
    if isinstance(val, str):
        return cls(address=val)
    if not isinstance(val, dict):
        raise DslError(f"{where}: expected mapping, got {type(val).__name__}")
    known = {"address", "address_in", "domain_in"}
    unknown = set(val.keys()) - known
    if unknown:
        raise DslError(f"{where}: unknown operator(s) {sorted(unknown)} on 'from'")
    return cls(
        address=val.get("address"),
        address_in=val.get("address_in"),
        domain_in=val.get("domain_in"),
    )


def _parse_string_predicate(cls, val: Any, *, where: str):
    # Shorthand: bare string => contains.
    if isinstance(val, str):
        return cls(contains=val)
    if not isinstance(val, dict):
        raise DslError(f"{where}: expected mapping or string, got {type(val).__name__}")
    known = {"contains", "starts_with", "ends_with", "regex", "equals"}
    unknown = set(val.keys()) - known
    if unknown:
        raise DslError(f"{where}: unknown operator(s) {sorted(unknown)}")
    return cls(**{k: val[k] for k in val.keys() & known})


def _parse_folder_predicate(val: Any, *, where: str) -> FolderP:
    if isinstance(val, str):
        return FolderP(equals=val)
    if not isinstance(val, dict):
        raise DslError(f"{where}: expected mapping or string")
    if "in" in val:
        return FolderP(in_=val["in"])
    if "equals" in val:
        return FolderP(equals=val["equals"])
    raise DslError(f"{where}: expected 'equals' or 'in'")


def _parse_age_predicate(val: Any, *, where: str) -> AgeP:
    if not isinstance(val, dict):
        raise DslError(f"{where}: expected mapping")
    known = {"older_than_days", "newer_than_days"}
    unknown = set(val.keys()) - known
    if unknown:
        raise DslError(f"{where}: unknown operator(s) {sorted(unknown)}")
    return AgeP(
        older_than_days=val.get("older_than_days"),
        newer_than_days=val.get("newer_than_days"),
    )


def _parse_categories_predicate(val: Any, *, where: str) -> CategoriesP:
    if isinstance(val, str):
        return CategoriesP(contains=val)
    if not isinstance(val, dict):
        raise DslError(f"{where}: expected mapping or string")
    known = {"contains", "equals", "in"}
    unknown = set(val.keys()) - known
    if unknown:
        raise DslError(f"{where}: unknown operator(s) {sorted(unknown)}")
    return CategoriesP(
        contains=val.get("contains"),
        equals=val.get("equals"),
        in_=val.get("in"),
    )


def _parse_action(raw: Any, *, where: str) -> Action:
    if not isinstance(raw, dict) or len(raw) != 1:
        raise DslError(
            f"{where}: each action must be a single-key mapping {{<action>: <args>}}"
        )
    (key, val), = raw.items()
    if key not in _ACTION_KEYS:
        raise DslError(
            f"{where}: unknown action {key!r} (known: {sorted(_ACTION_KEYS)})"
        )
    if key == "move":
        return MoveA(to_folder=_require_field(val, "to_folder", where=f"{where}.move"))
    if key == "copy":
        return CopyA(to_folder=_require_field(val, "to_folder", where=f"{where}.copy"))
    if key == "delete":
        return DeleteA()
    if key == "flag":
        if not isinstance(val, dict):
            raise DslError(f"{where}.flag: expected mapping")
        status = val.get("status", "flagged")
        if status not in ("notFlagged", "flagged", "complete"):
            raise DslError(f"{where}.flag.status: invalid {status!r}")
        return FlagA(status=status, due_days=val.get("due_days"))
    if key == "read":
        if not isinstance(val, bool):
            raise DslError(f"{where}.read: must be true|false")
        return ReadA(value=val)
    if key == "focus":
        if val not in ("focused", "other"):
            raise DslError(f"{where}.focus: must be 'focused' or 'other'")
        return FocusSetA(value=val)
    if key == "categorize":
        if not isinstance(val, dict):
            raise DslError(f"{where}.categorize: expected mapping")
        known = {"add", "remove", "set"}
        unknown = set(val.keys()) - known
        if unknown:
            raise DslError(f"{where}.categorize: unknown operator(s) {sorted(unknown)}")
        # Note: YAML 'set' → Python kwarg 'set_' (set is a builtin).
        return CategorizeA(
            add=val.get("add"),
            remove=val.get("remove"),
            set_=val.get("set"),
        )
    raise DslError(f"{where}: unhandled action {key!r}")  # pragma: no cover


def _require_field(raw: Any, field: str, *, where: str):
    if not isinstance(raw, dict):
        raise DslError(f"{where}: expected mapping")
    if field not in raw:
        raise DslError(f"{where}: missing required field {field!r}")
    return raw[field]
```

- [ ] **Step 4:** Run tests, verify pass:
```bash
uv run pytest tests/test_triage_dsl.py -v
```
Expected: all tests pass (~12 cases).

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/mail/triage/dsl.py tests/test_triage_dsl.py
git commit -m "feat(mail/triage): YAML DSL parser + typed RuleSet/Match/Action AST"
```

---

## Group 2 — Match evaluator

**Files:**
- Create: `src/m365ctl/mail/triage/match.py`
- Create: `tests/test_triage_match.py`

The evaluator runs over the catalog row dict shape (from `mail.catalog.queries`):
`{message_id, subject, from_address, parent_folder_path, received_at, is_read, is_flagged (via flag_status), has_attachments, importance, categories (comma-joined), inference_class, ...}`.

`now` is injected so tests don't depend on wall-clock.

### Task 2.1: TDD the evaluator

- [ ] **Step 1: Write failing tests** (`tests/test_triage_match.py`)

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from m365ctl.mail.triage.dsl import (
    AgeP, CategoriesP, FlaggedP, FocusP, FolderP, FromP, HasAttachmentsP,
    ImportanceP, Match, SubjectP, UnreadP,
)
from m365ctl.mail.triage.match import evaluate_match


_NOW = datetime(2026, 4, 25, tzinfo=timezone.utc)


def _row(**overrides):
    base = {
        "message_id": "m1",
        "subject": "Hello world",
        "from_address": "alice@example.com",
        "from_name": "Alice",
        "to_addresses": "me@example.com",
        "parent_folder_path": "Inbox",
        "received_at": _NOW - timedelta(days=2),
        "is_read": False,
        "flag_status": "notFlagged",
        "has_attachments": False,
        "importance": "normal",
        "categories": "",
        "inference_class": "focused",
    }
    base.update(overrides)
    return base


def test_match_empty_returns_true():
    assert evaluate_match(Match(), _row(), now=_NOW) is True


def test_from_address_in():
    m = Match(all_of=[FromP(address_in=["alice@example.com"])])
    assert evaluate_match(m, _row(), now=_NOW) is True
    assert evaluate_match(m, _row(from_address="bob@example.com"), now=_NOW) is False


def test_from_domain_in():
    m = Match(all_of=[FromP(domain_in=["example.com"])])
    assert evaluate_match(m, _row(), now=_NOW) is True
    assert evaluate_match(m, _row(from_address="x@other.com"), now=_NOW) is False


def test_subject_contains_case_insensitive():
    m = Match(all_of=[SubjectP(contains="HELLO")])
    assert evaluate_match(m, _row(), now=_NOW) is True


def test_subject_starts_with():
    m = Match(all_of=[SubjectP(starts_with="Hello")])
    assert evaluate_match(m, _row(), now=_NOW) is True
    assert evaluate_match(m, _row(subject="World hello"), now=_NOW) is False


def test_subject_regex():
    m = Match(all_of=[SubjectP(regex=r"^[A-Z][a-z]+\s")])
    assert evaluate_match(m, _row(), now=_NOW) is True


def test_folder_equals():
    m = Match(all_of=[FolderP(equals="Inbox")])
    assert evaluate_match(m, _row(), now=_NOW) is True
    assert evaluate_match(m, _row(parent_folder_path="Sent Items"), now=_NOW) is False


def test_folder_in():
    m = Match(all_of=[FolderP(in_=["Inbox", "Drafts"])])
    assert evaluate_match(m, _row(), now=_NOW) is True
    assert evaluate_match(m, _row(parent_folder_path="Sent Items"), now=_NOW) is False


def test_age_older_than_days():
    m = Match(all_of=[AgeP(older_than_days=1)])
    assert evaluate_match(m, _row(), now=_NOW) is True   # 2 days old
    assert evaluate_match(
        m, _row(received_at=_NOW - timedelta(hours=5)), now=_NOW
    ) is False


def test_age_newer_than_days():
    m = Match(all_of=[AgeP(newer_than_days=1)])
    assert evaluate_match(
        m, _row(received_at=_NOW - timedelta(hours=5)), now=_NOW
    ) is True
    assert evaluate_match(m, _row(), now=_NOW) is False


def test_unread_true():
    m = Match(all_of=[UnreadP(value=True)])
    assert evaluate_match(m, _row(is_read=False), now=_NOW) is True
    assert evaluate_match(m, _row(is_read=True), now=_NOW) is False


def test_is_flagged():
    m = Match(all_of=[FlaggedP(value=True)])
    assert evaluate_match(m, _row(flag_status="flagged"), now=_NOW) is True
    assert evaluate_match(m, _row(flag_status="notFlagged"), now=_NOW) is False


def test_has_attachments():
    m = Match(all_of=[HasAttachmentsP(value=True)])
    assert evaluate_match(m, _row(has_attachments=True), now=_NOW) is True
    assert evaluate_match(m, _row(has_attachments=False), now=_NOW) is False


def test_importance():
    m = Match(all_of=[ImportanceP(equals="high")])
    assert evaluate_match(m, _row(importance="high"), now=_NOW) is True
    assert evaluate_match(m, _row(importance="normal"), now=_NOW) is False


def test_focus():
    m = Match(all_of=[FocusP(equals="focused")])
    assert evaluate_match(m, _row(inference_class="focused"), now=_NOW) is True
    assert evaluate_match(m, _row(inference_class="other"), now=_NOW) is False


def test_categories_contains():
    m = Match(all_of=[CategoriesP(contains="Work")])
    assert evaluate_match(m, _row(categories="Work,Urgent"), now=_NOW) is True
    assert evaluate_match(m, _row(categories="Other"), now=_NOW) is False


def test_categories_in():
    m = Match(all_of=[CategoriesP(in_=["A", "B"])])
    assert evaluate_match(m, _row(categories="C,A"), now=_NOW) is True
    assert evaluate_match(m, _row(categories="X"), now=_NOW) is False


def test_all_of_requires_all():
    m = Match(all_of=[
        FromP(domain_in=["example.com"]),
        UnreadP(value=True),
    ])
    assert evaluate_match(m, _row(), now=_NOW) is True
    assert evaluate_match(m, _row(is_read=True), now=_NOW) is False


def test_any_of_requires_one():
    m = Match(any_of=[
        FromP(address="never@nope.com"),
        SubjectP(contains="Hello"),
    ])
    assert evaluate_match(m, _row(), now=_NOW) is True


def test_none_of_must_not_match():
    m = Match(
        all_of=[FolderP(equals="Inbox")],
        none_of=[FromP(domain_in=["spam.com"])],
    )
    assert evaluate_match(m, _row(), now=_NOW) is True
    assert evaluate_match(
        m, _row(from_address="bot@spam.com"), now=_NOW
    ) is False


def test_combined_all_any_none():
    m = Match(
        all_of=[FolderP(equals="Inbox")],
        any_of=[
            UnreadP(value=True),
            FlaggedP(value=True),
        ],
        none_of=[FromP(domain_in=["spam.com"])],
    )
    assert evaluate_match(m, _row(), now=_NOW) is True
    # in Inbox but read AND not flagged AND clean sender → any_of fails
    assert evaluate_match(
        m, _row(is_read=True, flag_status="notFlagged"), now=_NOW
    ) is False
```

- [ ] **Step 2:** Run, verify ImportError.

- [ ] **Step 3: Implement** (`src/m365ctl/mail/triage/match.py`)

```python
"""Predicate evaluator for the triage DSL.

Operates on dicts that look like rows from
``m365ctl.mail.catalog.queries`` (the catalog message schema).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from m365ctl.mail.triage.dsl import (
    AgeP, CategoriesP, FlaggedP, FocusP, FolderP, FromP,
    HasAttachmentsP, ImportanceP, Match, Predicate,
    SubjectP, UnreadP,
)


def evaluate_match(match: Match, row: dict[str, Any], *, now: datetime) -> bool:
    if match.all_of and not all(_eval(p, row, now=now) for p in match.all_of):
        return False
    if match.any_of and not any(_eval(p, row, now=now) for p in match.any_of):
        return False
    if match.none_of and any(_eval(p, row, now=now) for p in match.none_of):
        return False
    return True


def _eval(p: Predicate, row: dict[str, Any], *, now: datetime) -> bool:
    if isinstance(p, FromP):
        return _eval_from(p, row)
    if isinstance(p, SubjectP):
        return _eval_subject(p, row)
    if isinstance(p, FolderP):
        return _eval_folder(p, row)
    if isinstance(p, AgeP):
        return _eval_age(p, row, now=now)
    if isinstance(p, UnreadP):
        return bool(row.get("is_read") is False) is p.value
    if isinstance(p, FlaggedP):
        flagged = (row.get("flag_status") or "").lower() == "flagged"
        return flagged is p.value
    if isinstance(p, HasAttachmentsP):
        return bool(row.get("has_attachments")) is p.value
    if isinstance(p, CategoriesP):
        return _eval_categories(p, row)
    if isinstance(p, FocusP):
        return (row.get("inference_class") or "") == p.equals
    if isinstance(p, ImportanceP):
        return (row.get("importance") or "") == p.equals
    raise TypeError(f"unhandled predicate type: {type(p).__name__}")


def _eval_from(p: FromP, row: dict[str, Any]) -> bool:
    addr = (row.get("from_address") or "").lower()
    if not addr:
        return False
    if p.address is not None:
        if p.address.lower() != addr:
            return False
    if p.address_in is not None:
        if addr not in {a.lower() for a in p.address_in}:
            return False
    if p.domain_in is not None:
        domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
        if domain not in {d.lower() for d in p.domain_in}:
            return False
    return True


def _eval_subject(p: SubjectP, row: dict[str, Any]) -> bool:
    s = row.get("subject") or ""
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


def _eval_folder(p: FolderP, row: dict[str, Any]) -> bool:
    path = row.get("parent_folder_path") or ""
    if p.equals is not None:
        return path == p.equals
    if p.in_ is not None:
        return path in p.in_
    return True


def _eval_age(p: AgeP, row: dict[str, Any], *, now: datetime) -> bool:
    received = row.get("received_at")
    if received is None:
        return False
    if isinstance(received, str):
        received = datetime.fromisoformat(received.replace("Z", "+00:00"))
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    age = now - received
    if p.older_than_days is not None and age < timedelta(days=p.older_than_days):
        return False
    if p.newer_than_days is not None and age >= timedelta(days=p.newer_than_days):
        return False
    return True


def _eval_categories(p: CategoriesP, row: dict[str, Any]) -> bool:
    cats_raw = row.get("categories") or ""
    cats = [c for c in cats_raw.split(",") if c]
    if p.equals is not None and p.equals not in cats:
        return False
    if p.contains is not None and not any(p.contains in c for c in cats):
        return False
    if p.in_ is not None and not any(c in p.in_ for c in cats):
        return False
    return True
```

- [ ] **Step 4:** Run tests, verify pass:
```bash
uv run pytest tests/test_triage_match.py -v
```
Expected: ~22 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/mail/triage/match.py tests/test_triage_match.py
git commit -m "feat(mail/triage): predicate evaluator over catalog rows"
```

---

## Group 3 — Plan emitter

**Files:**
- Create: `src/m365ctl/mail/triage/plan.py`
- Create: `tests/test_triage_plan.py`

`build_plan` walks `RuleSet × catalog rows`, builds one `Operation` per (rule, action, matched-message). Each operation's `args` carries:
- `rule_name: str`
- per-action specifics (e.g. `to_folder` for move; `add`/`remove`/`set` for categorize; `due_days` for flag; etc.)

`drive_id` carries the mailbox UPN (matches the existing mail-domain convention: `me`, `upn:foo@x`, etc.). `item_id` is the catalog `message_id`. Disabled rules are skipped.

### Task 3.1: TDD plan emitter

- [ ] **Step 1: Write failing tests** (`tests/test_triage_plan.py`)

```python
from __future__ import annotations

from datetime import datetime, timezone

from m365ctl.mail.triage.dsl import (
    CategorizeA, FlagA, FromP, Match, MoveA, ReadA, Rule, RuleSet,
    UnreadP,
)
from m365ctl.mail.triage.plan import build_plan


_NOW = datetime(2026, 4, 25, tzinfo=timezone.utc)


def _ruleset(rules: list[Rule]) -> RuleSet:
    return RuleSet(version=1, mailbox="me", rules=rules)


def _row(message_id: str, **overrides):
    base = {
        "message_id": message_id,
        "subject": "x",
        "from_address": "alice@example.com",
        "parent_folder_path": "Inbox",
        "received_at": _NOW,
        "is_read": False,
        "flag_status": "notFlagged",
        "has_attachments": False,
        "importance": "normal",
        "categories": "",
        "inference_class": "focused",
    }
    base.update(overrides)
    return base


def test_no_matching_messages_yields_empty_plan():
    rs = _ruleset([
        Rule(name="r1", enabled=True,
             match=Match(all_of=[UnreadP(value=True)]),
             actions=[ReadA(value=True)]),
    ])
    plan = build_plan(rs, [_row("m1", is_read=True)],
                     mailbox_upn="me", source_cmd="x", scope="me", now=_NOW)
    assert plan.operations == []


def test_one_rule_one_action_one_message_per_op():
    rs = _ruleset([
        Rule(name="archive", enabled=True,
             match=Match(all_of=[FromP(domain_in=["example.com"])]),
             actions=[MoveA(to_folder="Archive")]),
    ])
    plan = build_plan(
        rs, [_row("m1"), _row("m2", from_address="bob@example.com"),
             _row("m3", from_address="x@other.com")],
        mailbox_upn="me", source_cmd="mail triage run --rules x.yaml",
        scope="me", now=_NOW,
    )
    assert len(plan.operations) == 2
    assert {op.item_id for op in plan.operations} == {"m1", "m2"}
    for op in plan.operations:
        assert op.action == "mail.move"
        assert op.args["to_folder"] == "Archive"
        assert op.args["rule_name"] == "archive"
        assert op.drive_id == "me"


def test_multiple_actions_emit_one_op_per_action_per_match():
    rs = _ruleset([
        Rule(name="combo", enabled=True,
             match=Match(all_of=[UnreadP(value=True)]),
             actions=[
                 CategorizeA(add=["X"]),
                 FlagA(status="flagged", due_days=2),
             ]),
    ])
    plan = build_plan(rs, [_row("m1"), _row("m2", is_read=True)],
                     mailbox_upn="me", source_cmd="x", scope="me", now=_NOW)
    actions = [op.action for op in plan.operations]
    assert actions == ["mail.categorize", "mail.flag"]
    assert plan.operations[1].args == {
        "rule_name": "combo", "status": "flagged", "due_days": 2,
    }


def test_disabled_rules_are_skipped():
    rs = _ruleset([
        Rule(name="off", enabled=False,
             match=Match(), actions=[ReadA(value=True)]),
        Rule(name="on", enabled=True,
             match=Match(), actions=[ReadA(value=True)]),
    ])
    plan = build_plan(rs, [_row("m1")], mailbox_upn="me", source_cmd="x",
                     scope="me", now=_NOW)
    rule_names = {op.args["rule_name"] for op in plan.operations}
    assert rule_names == {"on"}


def test_rules_stack_in_declaration_order():
    rs = _ruleset([
        Rule(name="first", enabled=True, match=Match(),
             actions=[ReadA(value=True)]),
        Rule(name="second", enabled=True, match=Match(),
             actions=[FlagA(status="flagged")]),
    ])
    plan = build_plan(rs, [_row("m1")], mailbox_upn="me", source_cmd="x",
                     scope="me", now=_NOW)
    assert [op.args["rule_name"] for op in plan.operations] == ["first", "second"]


def test_categorize_carries_add_remove_set():
    rs = _ruleset([
        Rule(name="cat", enabled=True, match=Match(),
             actions=[CategorizeA(add=["A"], remove=["B"], set_=["C", "D"])]),
    ])
    plan = build_plan(rs, [_row("m1")], mailbox_upn="me", source_cmd="x",
                     scope="me", now=_NOW)
    args = plan.operations[0].args
    assert args["add"] == ["A"]
    assert args["remove"] == ["B"]
    assert args["set"] == ["C", "D"]


def test_plan_metadata():
    rs = _ruleset([
        Rule(name="r", enabled=True, match=Match(),
             actions=[ReadA(value=True)]),
    ])
    plan = build_plan(rs, [_row("m1")], mailbox_upn="me",
                     source_cmd="mail triage run --rules x.yaml",
                     scope="me", now=_NOW)
    assert plan.version == 1
    assert plan.scope == "me"
    assert plan.source_cmd == "mail triage run --rules x.yaml"
```

- [ ] **Step 2:** Run, verify ImportError.

- [ ] **Step 3: Implement** (`src/m365ctl/mail/triage/plan.py`)

```python
"""Triage plan emitter — RuleSet × catalog rows → Plan."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from m365ctl.common.planfile import (
    PLAN_SCHEMA_VERSION, Operation, Plan, new_op_id,
)
from m365ctl.mail.triage.dsl import (
    Action, CategorizeA, CopyA, DeleteA, FlagA, FocusSetA, MoveA,
    ReadA, Rule, RuleSet,
)
from m365ctl.mail.triage.match import evaluate_match


def build_plan(
    ruleset: RuleSet,
    rows: Iterable[dict[str, Any]],
    *,
    mailbox_upn: str,
    source_cmd: str,
    scope: str,
    now: datetime,
) -> Plan:
    ops: list[Operation] = []
    rows_list = list(rows)
    for rule in ruleset.rules:
        if not rule.enabled:
            continue
        for row in rows_list:
            if not evaluate_match(rule.match, row, now=now):
                continue
            for action in rule.actions:
                ops.append(_op_for(rule, action, row, mailbox_upn=mailbox_upn))
    return Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at=now.isoformat(),
        source_cmd=source_cmd,
        scope=scope,
        operations=ops,
    )


def _op_for(rule: Rule, action: Action, row: dict, *, mailbox_upn: str) -> Operation:
    args: dict[str, Any] = {"rule_name": rule.name}
    if isinstance(action, MoveA):
        return _op("mail.move", row, mailbox_upn,
                   {**args, "to_folder": action.to_folder})
    if isinstance(action, CopyA):
        return _op("mail.copy", row, mailbox_upn,
                   {**args, "to_folder": action.to_folder})
    if isinstance(action, DeleteA):
        return _op("mail.delete.soft", row, mailbox_upn, args)
    if isinstance(action, FlagA):
        merged = {**args, "status": action.status}
        if action.due_days is not None:
            merged["due_days"] = action.due_days
        return _op("mail.flag", row, mailbox_upn, merged)
    if isinstance(action, ReadA):
        return _op("mail.read", row, mailbox_upn, {**args, "is_read": action.value})
    if isinstance(action, FocusSetA):
        return _op("mail.focus", row, mailbox_upn, {**args, "focus": action.value})
    if isinstance(action, CategorizeA):
        merged = dict(args)
        if action.add is not None:
            merged["add"] = list(action.add)
        if action.remove is not None:
            merged["remove"] = list(action.remove)
        if action.set_ is not None:
            merged["set"] = list(action.set_)
        return _op("mail.categorize", row, mailbox_upn, merged)
    raise TypeError(f"unhandled action: {type(action).__name__}")  # pragma: no cover


def _op(action: str, row: dict, mailbox_upn: str, args: dict) -> Operation:
    return Operation(
        op_id=new_op_id(),
        action=action,
        drive_id=mailbox_upn,
        item_id=row["message_id"],
        args=args,
        dry_run_result=_dry_run_summary(action, row, args),
    )


def _dry_run_summary(action: str, row: dict, args: dict) -> str:
    subj = (row.get("subject") or "")[:60]
    sender = row.get("from_address") or "?"
    rule = args.get("rule_name", "?")
    summary = {
        "mail.move":       f"would move → {args.get('to_folder')}",
        "mail.copy":       f"would copy → {args.get('to_folder')}",
        "mail.delete.soft": "would soft-delete → Deleted Items",
        "mail.flag":       f"would flag (status={args.get('status')})",
        "mail.read":       f"would mark read={args.get('is_read')}",
        "mail.focus":      f"would set focus={args.get('focus')}",
        "mail.categorize": f"would categorize (add={args.get('add')}, "
                           f"remove={args.get('remove')}, set={args.get('set')})",
    }.get(action, f"would {action}")
    return f"[{rule}] {summary}: {sender} | {subj}"
```

- [ ] **Step 4:** Run tests:
```bash
uv run pytest tests/test_triage_plan.py -v
```
Expected: 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/m365ctl/mail/triage/plan.py tests/test_triage_plan.py
git commit -m "feat(mail/triage): plan emitter — RuleSet × rows → tagged Operation list"
```

---

## Group 4 — Runner + CLI + bin wrapper + dispatcher

**Files:**
- Create: `src/m365ctl/mail/triage/runner.py`
- Create: `src/m365ctl/mail/cli/triage.py`
- Modify: `src/m365ctl/mail/cli/__main__.py` (route `triage` verb + add to `_USAGE`)
- Create: `bin/mail-triage`
- Create: `tests/test_triage_runner.py`
- Create: `tests/test_cli_mail_triage.py`

### Task 4.1: Runner — orchestrate validate / emit / execute

- [ ] **Step 1: Write failing tests** (`tests/test_triage_runner.py`)

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from m365ctl.common.planfile import Plan, Operation, PLAN_SCHEMA_VERSION
from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.triage.runner import (
    RunnerError, run_emit, run_execute, run_validate,
)


def _seed_messages(catalog_path: Path, rows: list[dict]) -> None:
    with open_catalog(catalog_path) as conn:
        for r in rows:
            base = {
                "mailbox_upn": "me",
                "message_id": r["message_id"],
                "internet_message_id": None,
                "conversation_id": None,
                "parent_folder_id": "fld-inbox",
                "parent_folder_path": r.get("parent_folder_path", "Inbox"),
                "subject": r.get("subject", ""),
                "from_address": r.get("from_address", "x@example.com"),
                "from_name": "X",
                "to_addresses": "",
                "received_at": r.get("received_at"),
                "sent_at": None,
                "is_read": r.get("is_read", False),
                "is_draft": False,
                "has_attachments": r.get("has_attachments", False),
                "importance": r.get("importance", "normal"),
                "flag_status": r.get("flag_status", "notFlagged"),
                "categories": r.get("categories", ""),
                "inference_class": r.get("inference_class", "focused"),
                "body_preview": "",
                "web_link": "",
                "size_estimate": 0,
                "is_deleted": False,
                "last_seen_at": "2026-04-25",
            }
            conn.execute(
                "INSERT INTO mail_messages VALUES ($mailbox_upn, $message_id, "
                "$internet_message_id, $conversation_id, $parent_folder_id, "
                "$parent_folder_path, $subject, $from_address, $from_name, "
                "$to_addresses, $received_at, $sent_at, $is_read, $is_draft, "
                "$has_attachments, $importance, $flag_status, $categories, "
                "$inference_class, $body_preview, $web_link, $size_estimate, "
                "$is_deleted, $last_seen_at)",
                base,
            )


def test_run_validate_ok(tmp_path: Path) -> None:
    p = tmp_path / "rules.yaml"
    p.write_text("""
version: 1
mailbox: me
rules:
  - name: r
    match: { unread: true }
    actions: [{ read: true }]
""")
    # No exception -> ok
    run_validate(p)


def test_run_validate_raises_on_bad_yaml(tmp_path: Path) -> None:
    p = tmp_path / "rules.yaml"
    p.write_text("""
version: 1
mailbox: me
rules:
  - name: bad
    match: { unread: not-a-bool }
    actions: [{ read: true }]
""")
    with pytest.raises(RunnerError):
        run_validate(p)


def test_run_emit_writes_plan(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text("""
version: 1
mailbox: me
rules:
  - name: archive
    match:
      all:
        - from: { domain_in: [example.com] }
        - folder: Inbox
    actions:
      - move: { to_folder: Archive }
""")
    catalog = tmp_path / "mail.duckdb"
    _seed_messages(catalog, [
        {"message_id": "m1", "from_address": "a@example.com"},
        {"message_id": "m2", "from_address": "b@other.com"},
    ])
    plan_out = tmp_path / "plan.json"
    plan = run_emit(
        rules_path=rules,
        catalog_path=catalog,
        mailbox_upn="me",
        scope="me",
        plan_out=plan_out,
    )
    assert plan_out.exists()
    assert len(plan.operations) == 1
    assert plan.operations[0].action == "mail.move"
    assert plan.operations[0].args["rule_name"] == "archive"
    assert plan.operations[0].item_id == "m1"


def test_run_execute_dispatches_per_action(tmp_path: Path) -> None:
    plan = Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at="2026-04-25T00:00:00",
        source_cmd="x",
        scope="me",
        operations=[
            Operation(op_id="op-1", action="mail.read",
                      drive_id="me", item_id="m1",
                      args={"rule_name": "r", "is_read": True},
                      dry_run_result=""),
            Operation(op_id="op-2", action="mail.flag",
                      drive_id="me", item_id="m1",
                      args={"rule_name": "r", "status": "flagged"},
                      dry_run_result=""),
        ],
    )
    fake_read = MagicMock(return_value=MagicMock(status="ok", error=None))
    fake_flag = MagicMock(return_value=MagicMock(status="ok", error=None))
    with patch.dict(
        "m365ctl.mail.triage.runner._EXECUTORS",
        {"mail.read": fake_read, "mail.flag": fake_flag},
        clear=False,
    ):
        results = run_execute(
            plan,
            cfg=MagicMock(),
            mailbox_spec="me",
            auth_mode="delegated",
            graph=MagicMock(),
            logger=MagicMock(),
        )
    assert len(results) == 2
    assert all(r.status == "ok" for r in results)
    fake_read.assert_called_once()
    fake_flag.assert_called_once()


def test_run_execute_continues_on_per_op_error() -> None:
    plan = Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at="2026-04-25T00:00:00",
        source_cmd="x",
        scope="me",
        operations=[
            Operation(op_id="op-1", action="mail.read",
                      drive_id="me", item_id="m1",
                      args={"rule_name": "r", "is_read": True},
                      dry_run_result=""),
            Operation(op_id="op-2", action="mail.read",
                      drive_id="me", item_id="m2",
                      args={"rule_name": "r", "is_read": True},
                      dry_run_result=""),
        ],
    )
    fake_read = MagicMock(side_effect=[
        MagicMock(status="error", error="404"),
        MagicMock(status="ok", error=None),
    ])
    with patch.dict(
        "m365ctl.mail.triage.runner._EXECUTORS",
        {"mail.read": fake_read},
        clear=False,
    ):
        results = run_execute(
            plan, cfg=MagicMock(), mailbox_spec="me",
            auth_mode="delegated", graph=MagicMock(), logger=MagicMock(),
        )
    assert [r.status for r in results] == ["error", "ok"]
```

- [ ] **Step 2:** Run, verify failures.

- [ ] **Step 3: Implement** (`src/m365ctl/mail/triage/runner.py`)

```python
"""Triage runner — orchestrate validate / emit / execute paths."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from m365ctl.common.audit import AuditLogger
from m365ctl.common.config import AuthMode, Config
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, Plan, write_plan
from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.triage.dsl import DslError, load_ruleset_from_yaml
from m365ctl.mail.triage.plan import build_plan


class RunnerError(RuntimeError):
    """Raised when validation/emit/execute fails."""


def run_validate(rules_path: Path | str) -> None:
    """Parse + shape-check the YAML; raise RunnerError on any issue."""
    try:
        load_ruleset_from_yaml(rules_path)
    except DslError as e:
        raise RunnerError(str(e)) from e
    except (FileNotFoundError, OSError) as e:
        raise RunnerError(f"cannot read {rules_path}: {e}") from e


def run_emit(
    *,
    rules_path: Path,
    catalog_path: Path,
    mailbox_upn: str,
    scope: str,
    plan_out: Path,
) -> Plan:
    """Load DSL, query the catalog, emit a Plan, write it to plan_out."""
    try:
        ruleset = load_ruleset_from_yaml(rules_path)
    except DslError as e:
        raise RunnerError(str(e)) from e

    rows = _candidate_rows(catalog_path=catalog_path, mailbox_upn=mailbox_upn)
    plan = build_plan(
        ruleset, rows,
        mailbox_upn=mailbox_upn,
        source_cmd=f"mail triage run --rules {rules_path}",
        scope=scope,
        now=datetime.now(timezone.utc),
    )
    write_plan(plan, plan_out)
    return plan


def _candidate_rows(*, catalog_path: Path, mailbox_upn: str) -> list[dict[str, Any]]:
    if not catalog_path.exists():
        raise RunnerError(
            f"catalog not built at {catalog_path}; run 'mail catalog refresh' first"
        )
    with open_catalog(catalog_path) as conn:
        cur = conn.execute(
            """
            SELECT message_id, subject, from_address, from_name,
                   parent_folder_path, received_at, is_read,
                   flag_status, has_attachments, importance,
                   categories, inference_class
            FROM mail_messages
            WHERE mailbox_upn = ? AND is_deleted = false
            """,
            [mailbox_upn],
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------- Execution path ----------

def _exec_move(op: Operation, *, cfg, mailbox_spec, auth_mode, graph, logger):
    from m365ctl.mail.mutate.move import execute_move
    return execute_move(
        op, graph, logger,
        mailbox_spec=mailbox_spec, auth_mode=auth_mode,
    )


def _exec_copy(op, *, cfg, mailbox_spec, auth_mode, graph, logger):
    from m365ctl.mail.mutate.copy import execute_copy
    return execute_copy(
        op, graph, logger,
        mailbox_spec=mailbox_spec, auth_mode=auth_mode,
    )


def _exec_delete(op, *, cfg, mailbox_spec, auth_mode, graph, logger):
    from m365ctl.mail.mutate.delete import execute_soft_delete
    return execute_soft_delete(
        op, graph, logger,
        mailbox_spec=mailbox_spec, auth_mode=auth_mode,
    )


def _exec_flag(op, *, cfg, mailbox_spec, auth_mode, graph, logger):
    from m365ctl.mail.mutate.flag import execute_flag
    return execute_flag(
        op, graph, logger,
        mailbox_spec=mailbox_spec, auth_mode=auth_mode,
    )


def _exec_read(op, *, cfg, mailbox_spec, auth_mode, graph, logger):
    from m365ctl.mail.mutate.read import execute_read
    return execute_read(
        op, graph, logger,
        mailbox_spec=mailbox_spec, auth_mode=auth_mode,
    )


def _exec_focus(op, *, cfg, mailbox_spec, auth_mode, graph, logger):
    from m365ctl.mail.mutate.focus import execute_focus
    return execute_focus(
        op, graph, logger,
        mailbox_spec=mailbox_spec, auth_mode=auth_mode,
    )


def _exec_categorize(op, *, cfg, mailbox_spec, auth_mode, graph, logger):
    from m365ctl.mail.mutate.categorize import execute_categorize
    return execute_categorize(
        op, graph, logger,
        mailbox_spec=mailbox_spec, auth_mode=auth_mode,
    )


_EXECUTORS = {
    "mail.move":         _exec_move,
    "mail.copy":         _exec_copy,
    "mail.delete.soft":  _exec_delete,
    "mail.flag":         _exec_flag,
    "mail.read":         _exec_read,
    "mail.focus":        _exec_focus,
    "mail.categorize":   _exec_categorize,
}


def run_execute(
    plan: Plan,
    *,
    cfg: Config,
    mailbox_spec: str,
    auth_mode: AuthMode,
    graph: GraphClient,
    logger: AuditLogger,
) -> list[Any]:
    """Dispatch each operation to its executor; collect per-op results.

    Continues past per-op failures so a single bad message doesn't abort
    the whole batch. Caller decides exit code from result statuses.
    """
    results = []
    for op in plan.operations:
        executor = _EXECUTORS.get(op.action)
        if executor is None:
            raise RunnerError(f"no executor for action {op.action!r}")
        try:
            r = executor(
                op, cfg=cfg, mailbox_spec=mailbox_spec,
                auth_mode=auth_mode, graph=graph, logger=logger,
            )
        except Exception as e:
            from types import SimpleNamespace
            r = SimpleNamespace(status="error", error=str(e))
        results.append(r)
    return results
```

- [ ] **Step 4: Verify executor signatures match.** This step is a check, not a code change. If any of `execute_move`, `execute_copy`, `execute_soft_delete`, `execute_flag`, `execute_read`, `execute_focus`, `execute_categorize` does NOT accept `(op, graph, logger, mailbox_spec=..., auth_mode=...)` exactly, you have two options:
  1. Adapt the wrapper in `runner.py` to call the executor with whatever signature it actually expects.
  2. (preferred when the existing CLI calls them with the same kwargs) leave runner as-is and adjust the test mock accordingly.

  Inspect each: `grep -n "def execute_" src/m365ctl/mail/mutate/{move,copy,delete,flag,read,focus,categorize}.py` and patch the wrappers if needed. Don't change the executors themselves; runner adapts to them.

- [ ] **Step 5: Run tests:**
```bash
uv run pytest tests/test_triage_runner.py -v
```
Expected: 5 tests pass.

- [ ] **Step 6: Commit**
```bash
git add src/m365ctl/mail/triage/runner.py tests/test_triage_runner.py
git commit -m "feat(mail/triage): runner — validate, emit Plan, dispatch per-action executors"
```

### Task 4.2: CLI wiring + bin wrapper + dispatcher

- [ ] **Step 1: Tests** (`tests/test_cli_mail_triage.py`)

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from m365ctl.mail.cli import triage as cli_triage


def _config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""
tenant_id = "t"
client_id = "c"
cert_path = "{tmp_path / 'c.pem'}"
cert_public = "{tmp_path / 'p.cer'}"
default_auth = "delegated"
[scope]
allow_drives = ["me"]
allow_mailboxes = ["me"]
[mail]
catalog_path = "{tmp_path / 'mail.duckdb'}"
[logging]
ops_dir = "{tmp_path / 'logs'}"
"""
    )
    return cfg


def test_validate_ok(tmp_path: Path, capsys) -> None:
    cfg = _config(tmp_path)
    rules = tmp_path / "r.yaml"
    rules.write_text("""
version: 1
mailbox: me
rules:
  - name: r
    match: { unread: true }
    actions: [{ read: true }]
""")
    rc = cli_triage.main(["validate", str(rules), "--config", str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ok" in out.lower() or "valid" in out.lower()


def test_validate_bad(tmp_path: Path, capsys) -> None:
    cfg = _config(tmp_path)
    rules = tmp_path / "r.yaml"
    rules.write_text("""
version: 1
mailbox: me
rules:
  - name: bad
    match: { unread: maybe }
    actions: [{ read: true }]
""")
    rc = cli_triage.main(["validate", str(rules), "--config", str(cfg)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unread" in err.lower()


def test_run_with_plan_out_does_not_execute(tmp_path: Path, capsys) -> None:
    cfg = _config(tmp_path)
    rules = tmp_path / "r.yaml"
    rules.write_text("""
version: 1
mailbox: me
rules:
  - name: r
    match: { unread: true }
    actions: [{ read: true }]
""")
    plan_out = tmp_path / "plan.json"
    fake_plan = MagicMock()
    fake_plan.operations = []
    with patch("m365ctl.mail.cli.triage.run_emit",
               return_value=fake_plan) as emit_mock:
        rc = cli_triage.main([
            "run", "--rules", str(rules),
            "--plan-out", str(plan_out),
            "--config", str(cfg),
        ])
    assert rc == 0
    emit_mock.assert_called_once()


def test_run_from_plan_requires_confirm(tmp_path: Path, capsys) -> None:
    cfg = _config(tmp_path)
    plan_in = tmp_path / "plan.json"
    plan_in.write_text("{}")  # contents irrelevant; CLI rejects missing --confirm first
    rc = cli_triage.main([
        "run", "--from-plan", str(plan_in),
        "--config", str(cfg),
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--confirm" in err
```

- [ ] **Step 2:** Run, verify ImportError.

- [ ] **Step 3: Implement** (`src/m365ctl/mail/cli/triage.py`)

```python
"""`m365ctl mail triage {validate, run}` — DSL → plan → confirm."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.config import load_config
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import load_plan
from m365ctl.common.safety import assert_mailbox_allowed
from m365ctl.mail.cli._common import load_and_authorize
from m365ctl.mail.triage.runner import (
    RunnerError, run_emit, run_execute, run_validate,
)


def _derive_mailbox_upn(spec: str) -> str:
    if spec == "me":
        return "me"
    if spec.startswith("upn:") or spec.startswith("shared:"):
        return spec.split(":", 1)[1]
    return spec


def _validate_main(args: argparse.Namespace) -> int:
    try:
        run_validate(args.rules)
    except RunnerError as e:
        print(f"invalid: {e}", file=sys.stderr)
        return 2
    print(f"ok: {args.rules} parses + validates cleanly.")
    return 0


def _run_main(args: argparse.Namespace) -> int:
    if args.rules and args.from_plan:
        print("error: --rules and --from-plan are mutually exclusive",
              file=sys.stderr)
        return 2
    if not args.rules and not args.from_plan:
        print("error: provide either --rules <yaml> or --from-plan <json>",
              file=sys.stderr)
        return 2

    cfg = load_config(Path(args.config))
    mailbox_spec = args.mailbox
    auth_mode = cfg.default_auth if mailbox_spec == "me" else "app-only"
    assert_mailbox_allowed(
        mailbox_spec, cfg, auth_mode=auth_mode, unsafe_scope=args.unsafe_scope,
    )
    mailbox_upn = _derive_mailbox_upn(mailbox_spec)

    if args.rules:
        # Plan path: emit (and optionally execute when --confirm).
        plan_out = Path(args.plan_out) if args.plan_out else None
        if plan_out is None and not args.confirm:
            print(
                "error: provide --plan-out (dry run) or --confirm (execute)",
                file=sys.stderr,
            )
            return 2
        try:
            if plan_out is None:
                # Implicit: stage to a temp file, execute, then discard.
                import tempfile
                plan_out = Path(tempfile.mkstemp(suffix=".plan.json")[1])
                emit_only = False
            else:
                emit_only = True
            plan = run_emit(
                rules_path=Path(args.rules),
                catalog_path=cfg.mail.catalog_path,
                mailbox_upn=mailbox_upn,
                scope=mailbox_spec,
                plan_out=plan_out,
            )
        except RunnerError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"plan: {len(plan.operations)} operations -> {plan_out}")
        if emit_only:
            return 0
    else:
        # --from-plan path: load + execute (only with --confirm).
        if not args.confirm:
            print("error: --from-plan requires --confirm", file=sys.stderr)
            return 2
        plan = load_plan(Path(args.from_plan))

    # Execute path.
    if not args.confirm:
        return 0  # already returned above for emit-only, but keep belt+braces
    _cfg, _auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    results = run_execute(
        plan,
        cfg=cfg,
        mailbox_spec=mailbox_spec,
        auth_mode=auth_mode,
        graph=graph,
        logger=logger,
    )
    ok = sum(1 for r in results if getattr(r, "status", "") == "ok")
    bad = len(results) - ok
    print(f"executed: {ok} ok, {bad} error(s)")
    return 1 if bad else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail triage")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--mailbox", default="me")
    p.add_argument("--unsafe-scope", action="store_true")
    p.add_argument("--confirm", action="store_true")
    sub = p.add_subparsers(dest="subcommand", required=True)

    v = sub.add_parser("validate", help="Parse + shape-check rules YAML.")
    v.add_argument("rules", help="Path to rules YAML.")

    r = sub.add_parser("run", help="Emit a plan from rules, or execute a plan.")
    r.add_argument("--rules", help="Path to rules YAML (emit mode).")
    r.add_argument("--from-plan", help="Path to plan.json (execute mode).")
    r.add_argument("--plan-out", help="Write plan to this path and exit.")

    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.subcommand == "validate":
        return _validate_main(args)
    if args.subcommand == "run":
        return _run_main(args)
    return 2
```

- [ ] **Step 4: Wire dispatcher** — edit `src/m365ctl/mail/cli/__main__.py`. Add to the elif chain (alphabetical-adjacent OK):

```python
    elif verb == "triage":
        from m365ctl.mail.cli.triage import main as f
```

And add to `_USAGE` under the Mutations block:
```python
    "  triage       triage validate <yaml> | triage run --rules <yaml> [--plan-out|--confirm]\n"
```

- [ ] **Step 5: bin wrapper** (`bin/mail-triage`):
```bash
#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec uv run --project "$REPO" python -m m365ctl mail triage "$@"
```

Then: `chmod +x bin/mail-triage`.

- [ ] **Step 6: Run tests:**
```bash
uv run pytest tests/test_cli_mail_triage.py tests/test_triage_runner.py -v
```
Expected: ~9 tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/m365ctl/mail/cli/triage.py src/m365ctl/mail/cli/__main__.py \
        bin/mail-triage tests/test_cli_mail_triage.py
git commit -m "feat(mail/cli): mail triage {validate,run} verbs + bin wrapper + dispatcher route"
```

---

## Group 5 — Examples + 0.8.0 release

### Task 5.1: Three example YAML files

**Files:**
- Create: `scripts/mail/rules/triage.example.yaml`
- Create: `scripts/mail/rules/archive-newsletters.yaml`
- Create: `scripts/mail/rules/daily-triage.yaml`

- [ ] **Step 1:** `scripts/mail/rules/triage.example.yaml` — kitchen sink reference covering every shipped predicate + action. All addresses use `example.com`.

```yaml
# m365ctl mail triage — reference rules file.
# Shows every predicate and action shipped in Phase 10.
# Run dry: m365ctl mail triage run --rules triage.example.yaml --plan-out /tmp/p.json
version: 1
mailbox: me

rules:
  - name: archive-newsletters
    enabled: true
    match:
      all:
        - from: { domain_in: [newsletter.example.com, news.example.com] }
        - age: { older_than_days: 7 }
        - folder: Inbox
    actions:
      - categorize: { add: [Archived/Newsletter] }
      - move: { to_folder: Archive/Newsletters }

  - name: urgent-from-leadership
    match:
      all:
        - unread: true
        - folder: Inbox
        - from: { address_in: [alice@example.com, bob@example.com] }
    actions:
      - categorize: { add: [Triage/Followup] }
      - flag: { status: flagged, due_days: 2 }
      - focus: focused

  - name: receipts-and-invoices
    match:
      any:
        - subject: { contains: invoice }
        - subject: { regex: "(?i)receipt|order #" }
      none:
        - folder: Spam
    actions:
      - categorize: { add: [Finance] }
      - read: false   # keep unread until reviewed

  - name: high-importance-flag
    match: { importance: high }
    actions:
      - flag: { status: flagged }

  - name: stale-attachments
    match:
      all:
        - has_attachments: true
        - age: { older_than_days: 60 }
        - folder: Inbox
    actions:
      - move: { to_folder: Archive/Attachments }
```

- [ ] **Step 2:** `scripts/mail/rules/archive-newsletters.yaml` — minimal newsletter archiver.

```yaml
# Archive newsletter mail older than a week into Archive/Newsletters.
version: 1
mailbox: me
rules:
  - name: archive-newsletters
    match:
      all:
        - from: { domain_in: [newsletter.example.com, weekly.example.com] }
        - age: { older_than_days: 7 }
        - folder: Inbox
    actions:
      - move: { to_folder: Archive/Newsletters }
```

- [ ] **Step 3:** `scripts/mail/rules/daily-triage.yaml` — opinionated daily-driver.

```yaml
# Daily triage routine. Run each morning before opening Outlook.
version: 1
mailbox: me
rules:
  - name: clear-read-newsletters
    match:
      all:
        - from: { domain_in: [newsletter.example.com] }
        - unread: false
      none:
        - is_flagged: true
    actions:
      - delete: {}    # soft-delete; reversible via `mail undo`

  - name: flag-leadership
    match:
      all:
        - unread: true
        - from: { address_in: [alice@example.com, bob@example.com] }
    actions:
      - flag: { status: flagged, due_days: 1 }

  - name: focus-noise
    match:
      all:
        - folder: Inbox
        - from: { domain_in: [notifications.example.com] }
    actions:
      - focus: other
```

- [ ] **Step 4:** Validate all three with the new validator (sanity check):
```bash
for f in scripts/mail/rules/*.yaml; do
  uv run python -m m365ctl mail triage validate "$f" || { echo "FAIL: $f"; exit 1; }
done
```
Expected: each one prints `ok: ...`.

- [ ] **Step 5: Commit**
```bash
git add scripts/mail/rules/
git commit -m "docs(mail/triage): three example rule files (all use example.com domains)"
```

### Task 5.2: 0.8.0 release

- [ ] **Step 1:** Edit `pyproject.toml`: bump `0.7.0` → `0.8.0`.

- [ ] **Step 2:** Prepend to `CHANGELOG.md`:

```markdown
## 0.8.0 — Phase 10: triage DSL + engine

### Added
- `m365ctl.mail.triage.{dsl,match,plan,runner}` — YAML rules → typed
  `RuleSet` AST → predicate evaluator → tagged `Plan`.
- CLI: `mail triage validate <yaml>` (CI-friendly, no Graph calls) and
  `mail triage run --rules <yaml> [--plan-out <p> | --confirm]`. Bin
  wrapper `bin/mail-triage`.
- Three reference rule files in `scripts/mail/rules/` — every example
  uses `example.com` domains only.
- New `pyyaml>=6.0` runtime dependency.

### Predicates shipped
`from`, `subject`, `folder`, `age`, `unread`, `is_flagged`,
`has_attachments`, `categories`, `focus`, `importance`. Composable with
`all` / `any` / `none`.

### Actions shipped
`move`, `copy`, `delete` (soft), `flag`, `read`, `focus`, `categorize`
(add/remove/set). Each emitted op carries `args.rule_name` for
attribution; existing audit + undo intact.

### Deferred
- `to`, `cc`, `body`, `thread`, `headers` predicates — need either Graph
  fetches or richer catalog coverage. Phase 10.x.
- KQL pushdown for "obvious" predicates — Phase 7 catalog covers the
  needed surface area, so the first cut runs entirely local. Phase 10.x.
```

- [ ] **Step 3:** Add to `README.md` under the Mail section:
```markdown
- **Triage DSL (Phase 10):** `mail triage validate <yaml>` and
  `mail triage run --rules <yaml> [--plan-out|--confirm]` — YAML rules
  match against the local catalog and emit a tagged plan that reuses
  the existing audit/undo paths. Examples in `scripts/mail/rules/`.
```

- [ ] **Step 4:** `uv sync` to regenerate `uv.lock`.

- [ ] **Step 5:** Full suite + lint + types:
```bash
uv run pytest --tb=no -q
uv run mypy src/m365ctl
uv run ruff check
```
Expected: all green; baseline 573 + 12 (DSL) + 22 (match) + 7 (plan) + 5 (runner) + 4 (CLI) ≈ 623 passing.

- [ ] **Step 6:** Commit version + lockfile separately (no-amend rule):
```bash
git add pyproject.toml CHANGELOG.md README.md
git commit -m "chore(release): bump to 0.8.0 + Phase 10 triage changelog/README"

git add uv.lock
git commit -m "chore(release): sync uv.lock for 0.8.0"
```

### Task 5.3: Push, PR, merge, tag

- [ ] **Step 1: Push**
```bash
git push -u origin phase-10-triage-dsl
```

- [ ] **Step 2: Open PR**
```bash
gh pr create --title "Phase 10: triage DSL + engine → 0.8.0" --body "$(cat <<'EOF'
## Summary
- `m365ctl.mail.triage.{dsl,match,plan,runner}` — YAML rules pipeline that emits tagged `Plan` files and reuses Phase 3/4 mutate executors.
- New CLI: `mail triage validate <yaml>` (no Graph calls; CI-friendly) and `mail triage run --rules <yaml> [--plan-out <p> | --confirm]`.
- Three reference YAMLs under `scripts/mail/rules/` using only `example.com` domains.
- Bumps to 0.8.0; CHANGELOG + README updated; `pyyaml>=6.0` added; `uv.lock` regenerated.

## Predicates / actions
Predicates: `from`, `subject`, `folder`, `age`, `unread`, `is_flagged`, `has_attachments`, `categories`, `focus`, `importance`. Composable via `all`/`any`/`none`.

Actions: `move`, `copy`, `delete` (soft), `flag`, `read`, `focus`, `categorize` (add/remove/set). Each operation carries `args.rule_name`.

## Deferred (Phase 10.x)
- `to`/`cc`/`body`/`thread`/`headers` predicates.
- KQL pushdown.

## Test plan
- [ ] CI green on 3.11/3.12/3.13 × {ubuntu, macos}.
- [ ] Local: `uv run pytest` ≈ 623 passing, mypy 0 errors, ruff clean.
- [ ] All three example YAMLs pass `mail triage validate`.
EOF
)"
```

- [ ] **Step 3: Wait for CI green and merge:**
```bash
gh pr checks --watch
gh pr merge --squash --delete-branch
```

- [ ] **Step 4: Sync and tag:**
```bash
git checkout main
git pull --ff-only
git tag -a v0.8.0 -m "Phase 10: triage DSL + engine"
git push origin v0.8.0
```

---

## Self-review checklist (run at end of plan-write)

**Spec coverage (§19 Phase 10 + §14):**
- ✅ `m365ctl.mail.triage.dsl` — YAML parser → typed AST → Group 1.
- ⚠️ `m365ctl.mail.triage.match` — predicate evaluator with KQL pushdown. **Pushdown deferred** — first cut runs all predicates locally over the catalog (which Phase 7 already populated). Spec §14.4 promises hybrid; we ship local-only with a clear CHANGELOG note.
- ✅ `m365ctl.mail.triage.plan` — Operation list tagged with `args.rule_name` → Group 3.
- ✅ CLI `mail-triage {run, validate}` → Group 4.
- ✅ Examples (generic; `example.com` only) → Group 5.
- ✅ Tests: DSL round-trip (Task 1.2), match evaluator (Task 2.1), end-to-end dry-run (Task 4.1 `test_run_emit_writes_plan`).
- ⚠️ Spec said bump to 0.12.0 sequentially — we bump to 0.8.0 because we skipped 5b/6/8/9.

**Acceptance:**
- ✅ Dry-run first: `--plan-out` writes plan and never mutates.
- ✅ Rule attribution: each op's `args.rule_name` set in plan emitter (verified in tests).
- ✅ Deterministic ordering: rules iterate top-to-bottom, multiple matches stack.
- ✅ Disabled rules skipped.

**Placeholder scan:** searched plan for "TODO"/"TBD"/"implement later" — none.

**Type consistency:**
- `Match` defined in dsl.py used by match.py and plan.py — same shape.
- `RuleSet` / `Rule` field names consistent across all four files.
- `Operation.args["rule_name"]` keyed identically in plan emitter and tests.
- Action class names (`MoveA`/`CopyA`/`FlagA`/etc.) used identically across dsl.py, plan.py, tests.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-25-phase-10-triage-dsl.md`.

Execution: subagent-driven-development (per established Phase 0–7 cadence). Branch `phase-10-triage-dsl` already created off `main`. Dispatch one implementer per group with two-stage review (spec → code-quality), commit per task, push and PR autonomously when CI is green.
