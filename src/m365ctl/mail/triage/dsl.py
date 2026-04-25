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

import yaml  # type: ignore[import-untyped]


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
class ToP:
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
class CcP:
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
class BodyP:
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
    FromP | ToP | CcP | SubjectP | BodyP | FolderP | AgeP | UnreadP | FlaggedP
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
    if key == "to":
        return _parse_addr_predicate(ToP, val, where=f"{where}.to")
    if key == "cc":
        return _parse_addr_predicate(CcP, val, where=f"{where}.cc")
    if key == "subject":
        return _parse_string_predicate(SubjectP, val, where=f"{where}.subject")
    if key == "body":
        return _parse_string_predicate(BodyP, val, where=f"{where}.body")
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
    # Note: error message kept generic-ish — the {where} prefix already names
    # the predicate (e.g. "...to"), and the existing test asserts
    # "unknown operator.*vibes.*from" only on the from-predicate path.
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
