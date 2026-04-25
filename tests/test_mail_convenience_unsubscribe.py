"""Tests for m365ctl.mail.convenience.unsubscribe — header parser."""
from __future__ import annotations


def test_parse_list_unsubscribe_mailto_and_https():
    from m365ctl.mail.convenience.unsubscribe import (
        UnsubscribeMethod,
        parse_list_unsubscribe,
    )
    got = parse_list_unsubscribe(
        "<mailto:unsub@example.com>, <https://example.com/u?id=42>"
    )
    assert got == [
        UnsubscribeMethod(kind="mailto", target="mailto:unsub@example.com"),
        UnsubscribeMethod(kind="https", target="https://example.com/u?id=42"),
    ]


def test_parse_list_unsubscribe_multiple_https():
    from m365ctl.mail.convenience.unsubscribe import parse_list_unsubscribe
    got = parse_list_unsubscribe(
        "<https://a.example.com/u>, <https://b.example.com/u>"
    )
    assert len(got) == 2
    assert all(m.kind == "https" for m in got)


def test_parse_list_unsubscribe_empty():
    from m365ctl.mail.convenience.unsubscribe import parse_list_unsubscribe
    assert parse_list_unsubscribe("") == []


def test_parse_list_unsubscribe_malformed_entries_discarded():
    from m365ctl.mail.convenience.unsubscribe import parse_list_unsubscribe
    got = parse_list_unsubscribe(
        "garbage, <mailto:ok@example.com>, <ftp://nope.example.com>, <>"
    )
    assert len(got) == 1
    assert got[0].target == "mailto:ok@example.com"


def test_discover_methods_extracts_from_graph_headers():
    from m365ctl.mail.convenience.unsubscribe import discover_methods
    message = {
        "internetMessageHeaders": [
            {"name": "From", "value": "newsletter@example.com"},
            {"name": "List-Unsubscribe",
             "value": "<https://example.com/u?id=42>, <mailto:unsub@example.com>"},
        ],
    }
    methods = discover_methods(message)
    kinds = [m.kind for m in methods]
    assert kinds == ["https", "mailto"]
    assert all(not m.one_click for m in methods)


def test_discover_methods_marks_one_click_for_https():
    from m365ctl.mail.convenience.unsubscribe import discover_methods
    message = {
        "internetMessageHeaders": [
            {"name": "List-Unsubscribe",
             "value": "<https://example.com/u?id=42>, <mailto:unsub@example.com>"},
            {"name": "List-Unsubscribe-Post",
             "value": "List-Unsubscribe=One-Click"},
        ],
    }
    methods = discover_methods(message)
    by_kind = {m.kind: m for m in methods}
    assert by_kind["https"].one_click is True
    # mailto methods are never one-click — that flag only applies to URLs.
    assert by_kind["mailto"].one_click is False


def test_discover_methods_no_header_returns_empty():
    from m365ctl.mail.convenience.unsubscribe import discover_methods
    assert discover_methods({"internetMessageHeaders": []}) == []
    assert discover_methods({}) == []
