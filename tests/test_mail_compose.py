"""Tests for m365ctl.mail.compose — pure payload/recipient helpers."""
from __future__ import annotations

import pytest

from m365ctl.mail.compose import (
    BodyFormatError,
    build_message_payload,
    count_external_recipients,
    parse_recipients,
)


def test_parse_recipients_plain_addresses():
    assert parse_recipients(["alice@example.com", "bob@example.com"]) == [
        {"emailAddress": {"address": "alice@example.com"}},
        {"emailAddress": {"address": "bob@example.com"}},
    ]


def test_parse_recipients_name_plus_angle():
    assert parse_recipients(["Alice <alice@example.com>"]) == [
        {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
    ]


def test_parse_recipients_strips_whitespace():
    assert parse_recipients(["  alice@example.com  "]) == [
        {"emailAddress": {"address": "alice@example.com"}},
    ]


def test_parse_recipients_empty_returns_empty():
    assert parse_recipients([]) == []


def test_parse_recipients_rejects_non_email():
    with pytest.raises(ValueError):
        parse_recipients(["not-an-email"])


# ---- build_message_payload -------------------------------------------------

def test_build_message_payload_minimal_text():
    payload = build_message_payload(
        subject="Hello",
        body="Hi there",
        body_type="text",
        to=["alice@example.com"],
    )
    assert payload == {
        "subject": "Hello",
        "body": {"contentType": "text", "content": "Hi there"},
        "toRecipients": [{"emailAddress": {"address": "alice@example.com"}}],
    }


def test_build_message_payload_full_cc_bcc_importance_html():
    payload = build_message_payload(
        subject="Project update",
        body="<p>Status report</p>",
        body_type="html",
        to=["alice@example.com"],
        cc=["bob@example.com"],
        bcc=["auditor@example.com"],
        importance="high",
    )
    assert payload["body"]["contentType"] == "html"
    assert payload["body"]["content"] == "<p>Status report</p>"
    assert payload["ccRecipients"] == [{"emailAddress": {"address": "bob@example.com"}}]
    assert payload["bccRecipients"] == [{"emailAddress": {"address": "auditor@example.com"}}]
    assert payload["importance"] == "high"


def test_build_message_payload_default_body_type_is_text():
    payload = build_message_payload(
        subject="x", body="y", to=["a@example.com"],
    )
    assert payload["body"]["contentType"] == "text"


def test_build_message_payload_rejects_empty_subject_when_required():
    with pytest.raises(BodyFormatError):
        build_message_payload(
            subject="",
            body="body",
            to=["a@example.com"],
            require_subject=True,
        )


def test_build_message_payload_empty_subject_allowed_by_default():
    payload = build_message_payload(
        subject="",
        body="body",
        to=["a@example.com"],
    )
    assert payload["subject"] == ""


# ---- count_external_recipients ---------------------------------------------

def test_count_external_recipients_no_internal_domain():
    recips = parse_recipients(["alice@example.com", "bob@example.com"])
    assert count_external_recipients(recips, internal_domain=None) == 2


def test_count_external_recipients_with_internal_domain():
    recips = parse_recipients([
        "alice@example.com",
        "colleague@contoso.com",
        "contractor@example.com",
    ])
    assert count_external_recipients(recips, internal_domain="contoso.com") == 2


def test_count_external_recipients_case_insensitive_domain():
    recips = parse_recipients(["CoLLeAgue@CONTOSO.com"])
    assert count_external_recipients(recips, internal_domain="contoso.com") == 0


def test_count_external_recipients_collapses_recipient_lists():
    to = parse_recipients(["alice@example.com"])
    cc = parse_recipients(["bob@contoso.com"])
    bcc = parse_recipients(["carol@external.com"])
    combined = to + cc + bcc
    assert count_external_recipients(combined, internal_domain="contoso.com") == 2
