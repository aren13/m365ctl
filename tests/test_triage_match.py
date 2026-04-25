from __future__ import annotations

from datetime import datetime, timedelta, timezone

from m365ctl.mail.triage.dsl import (
    AgeP, BodyP, CategoriesP, FlaggedP, FocusP, FolderP, FromP, HasAttachmentsP,
    ImportanceP, Match, SubjectP, ToP, UnreadP,
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


def test_to_address_in_to_addresses():
    m = Match(all_of=[ToP(address="bob@example.com")])
    assert evaluate_match(
        m, _row(to_addresses="alice@example.com,bob@example.com"), now=_NOW
    ) is True
    assert evaluate_match(
        m, _row(to_addresses="alice@example.com"), now=_NOW
    ) is False


def test_to_domain_in_matches_any_address():
    m = Match(all_of=[ToP(domain_in=["example.com"])])
    assert evaluate_match(
        m, _row(to_addresses="bob@other.com,carol@example.com"), now=_NOW
    ) is True
    assert evaluate_match(
        m, _row(to_addresses="bob@other.com"), now=_NOW
    ) is False


def test_body_contains_case_insensitive():
    m = Match(all_of=[BodyP(contains="INVOICE")])
    assert evaluate_match(
        m, _row(body_preview="Please find the invoice attached"), now=_NOW
    ) is True


def test_body_does_not_match_when_preview_empty_or_null():
    m = Match(all_of=[BodyP(contains="anything")])
    assert evaluate_match(m, _row(body_preview=""), now=_NOW) is False
    assert evaluate_match(m, _row(body_preview=None), now=_NOW) is False


def test_body_regex_starts_ends_with():
    assert evaluate_match(
        Match(all_of=[BodyP(regex=r"^[A-Z]")]),
        _row(body_preview="Hello body"),
        now=_NOW,
    ) is True
    assert evaluate_match(
        Match(all_of=[BodyP(starts_with="Hello")]),
        _row(body_preview="Hello body"),
        now=_NOW,
    ) is True
    assert evaluate_match(
        Match(all_of=[BodyP(ends_with="body")]),
        _row(body_preview="Hello body"),
        now=_NOW,
    ) is True


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
