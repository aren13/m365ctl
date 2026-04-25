from __future__ import annotations

from pathlib import Path

import pytest

from m365ctl.mail.triage.dsl import (
    AgeP, BodyP, CategorizeA, CcP, DslError, FlagA, FolderP, FromP, ImportanceP,
    MoveA, SubjectP, ThreadP, ToP, UnreadP,
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


def test_to_predicate_domain_in(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: to-domain
    match: { to: { domain_in: [example.com] } }
    actions: [{ move: { to_folder: X } }]
""")
    rs = load_ruleset_from_yaml(p)
    assert rs.rules[0].match.all_of == [ToP(domain_in=["example.com"])]


def test_to_predicate_string_shorthand(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: to-addr
    match: { to: alice@example.com }
    actions: [{ move: { to_folder: X } }]
""")
    rs = load_ruleset_from_yaml(p)
    assert rs.rules[0].match.all_of == [ToP(address="alice@example.com")]


def test_body_predicate_contains(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: body-contains
    match: { body: { contains: invoice } }
    actions: [{ move: { to_folder: X } }]
""")
    rs = load_ruleset_from_yaml(p)
    assert rs.rules[0].match.all_of == [BodyP(contains="invoice")]


def test_body_predicate_string_shorthand(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: body-shorthand
    match: { body: invoice }
    actions: [{ move: { to_folder: X } }]
""")
    rs = load_ruleset_from_yaml(p)
    assert rs.rules[0].match.all_of == [BodyP(contains="invoice")]


def test_body_predicate_unknown_operator_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: bad-body
    match: { body: { vibes: good } }
    actions: [{ move: { to_folder: X } }]
""")
    with pytest.raises(DslError, match="unknown operator.*vibes"):
        load_ruleset_from_yaml(p)


def test_cc_predicate_domain_in(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: cc-domain
    match: { cc: { domain_in: [example.com] } }
    actions: [{ move: { to_folder: X } }]
""")
    rs = load_ruleset_from_yaml(p)
    assert rs.rules[0].match.all_of == [CcP(domain_in=["example.com"])]


def test_cc_predicate_string_shorthand(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: cc-addr
    match: { cc: alice@example.com }
    actions: [{ move: { to_folder: X } }]
""")
    rs = load_ruleset_from_yaml(p)
    assert rs.rules[0].match.all_of == [CcP(address="alice@example.com")]


def test_thread_has_reply_false(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: follow-up
    match: { thread: { has_reply: false } }
    actions: [{ flag: { status: flagged } }]
""")
    rs = load_ruleset_from_yaml(p)
    assert rs.rules[0].match.all_of == [ThreadP(has_reply=False)]


def test_thread_has_reply_true(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: replied
    match: { thread: { has_reply: true } }
    actions: [{ move: { to_folder: Archive } }]
""")
    rs = load_ruleset_from_yaml(p)
    assert rs.rules[0].match.all_of == [ThreadP(has_reply=True)]


def test_thread_has_reply_must_be_bool(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: bad-thread
    match: { thread: { has_reply: "maybe" } }
    actions: [{ move: { to_folder: X } }]
""")
    with pytest.raises(DslError, match="has_reply.*must be true|false"):
        load_ruleset_from_yaml(p)


def test_thread_unknown_operator_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
mailbox: me
rules:
  - name: bad-thread
    match: { thread: { vibes: cool } }
    actions: [{ move: { to_folder: X } }]
""")
    with pytest.raises(DslError, match="unknown operator.*vibes"):
        load_ruleset_from_yaml(p)


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
