"""Tests for m365ctl.mail.messages — readers over a mocked Graph client."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from m365ctl.common.graph import GraphError
from m365ctl.mail.messages import (
    MessageListFilters,
    find_by_internet_message_id,
    get_message,
    get_thread,
    list_messages,
    search_messages_graph,
)
from m365ctl.mail.models import Message


def _graph_with_single_page(pages: list[dict]) -> MagicMock:
    """Stub GraphClient.get_paginated to yield ``pages``."""
    graph = MagicMock()
    graph.get_paginated.return_value = iter([(p.get("value", []), None) for p in pages])
    return graph


def _msg_raw(msg_id: str = "m1", folder_id: str = "folder-1") -> dict:
    return {
        "id": msg_id,
        "internetMessageId": f"<{msg_id}@example.com>",
        "conversationId": f"conv-{msg_id}",
        "conversationIndex": "AQ==",
        "parentFolderId": folder_id,
        "subject": f"Subj {msg_id}",
        "sender": {"emailAddress": {"name": "A", "address": "a@example.com"}},
        "from": {"emailAddress": {"name": "A", "address": "a@example.com"}},
        "toRecipients": [],
        "ccRecipients": [],
        "bccRecipients": [],
        "replyTo": [],
        "receivedDateTime": "2026-04-24T10:00:00Z",
        "sentDateTime": "2026-04-24T09:59:55Z",
        "isRead": False,
        "isDraft": False,
        "hasAttachments": False,
        "importance": "normal",
        "flag": {"flagStatus": "notFlagged"},
        "categories": [],
        "inferenceClassification": "focused",
        "bodyPreview": "...",
        "webLink": "https://x",
        "changeKey": "ck",
    }


def test_list_messages_basic_inbox():
    graph = _graph_with_single_page([{"value": [_msg_raw("m1"), _msg_raw("m2")]}])
    out = list(list_messages(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        folder_id="AAMkAD..inbox",
        parent_folder_path="/Inbox",
    ))
    assert len(out) == 2
    assert all(isinstance(m, Message) for m in out)
    assert [m.id for m in out] == ["m1", "m2"]
    call_args = graph.get_paginated.call_args
    assert call_args.args[0] == "/me/mailFolders/AAMkAD..inbox/messages"


def test_list_messages_app_only_routes_via_users_upn():
    graph = _graph_with_single_page([{"value": [_msg_raw()]}])
    list(list_messages(
        graph,
        mailbox_spec="upn:bob@example.com",
        auth_mode="app-only",
        folder_id="AAMkAD..inbox",
        parent_folder_path="/Inbox",
    ))
    url = graph.get_paginated.call_args.args[0]
    assert url == "/users/bob@example.com/mailFolders/AAMkAD..inbox/messages"


def test_list_messages_filters_odata():
    graph = _graph_with_single_page([{"value": []}])
    filters = MessageListFilters(
        unread=True,
        from_address="alice@example.com",
        subject_contains="meeting",
        since="2026-04-20T00:00:00Z",
        until="2026-04-24T00:00:00Z",
        has_attachments=True,
        importance="high",
        focus="focused",
        category="Followup",
    )
    list(list_messages(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        folder_id="inbox",
        parent_folder_path="/Inbox",
        filters=filters,
        limit=10,
    ))
    kwargs = graph.get_paginated.call_args.kwargs
    params = kwargs["params"]
    f = params["$filter"]
    assert "isRead eq false" in f
    assert "from/emailAddress/address eq 'alice@example.com'" in f
    assert "contains(subject, 'meeting')" in f
    assert "receivedDateTime ge 2026-04-20T00:00:00Z" in f
    assert "receivedDateTime le 2026-04-24T00:00:00Z" in f
    assert "hasAttachments eq true" in f
    assert "importance eq 'high'" in f
    assert "inferenceClassification eq 'focused'" in f
    assert "categories/any(c:c eq 'Followup')" in f
    assert params["$top"] == 10
    assert params["$orderby"] == "receivedDateTime desc"


def test_list_messages_limit_stops_iteration():
    graph = _graph_with_single_page([
        {"value": [_msg_raw(f"m{i}") for i in range(50)]},
    ])
    out = list(list_messages(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        folder_id="inbox",
        parent_folder_path="/Inbox",
        limit=5,
    ))
    assert len(out) == 5


def test_list_messages_empty_filter_omits_dollar_filter():
    graph = _graph_with_single_page([{"value": []}])
    list(list_messages(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        folder_id="inbox",
        parent_folder_path="/Inbox",
    ))
    params = graph.get_paginated.call_args.kwargs["params"]
    assert "$filter" not in params


def test_list_messages_odata_escapes_single_quotes_in_subject():
    graph = _graph_with_single_page([{"value": []}])
    list(list_messages(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        folder_id="inbox",
        parent_folder_path="/Inbox",
        filters=MessageListFilters(subject_contains="it's urgent"),
    ))
    f = graph.get_paginated.call_args.kwargs["params"]["$filter"]
    # Single quotes get doubled per OData quoting rules.
    assert "contains(subject, 'it''s urgent')" in f


def test_get_message_with_expand_attachments():
    graph = MagicMock()
    graph.get.return_value = _msg_raw("m1")
    m = get_message(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        message_id="m1",
        with_attachments=True,
    )
    assert m.id == "m1"
    url = graph.get.call_args.args[0]
    assert url == "/me/messages/m1"
    params = graph.get.call_args.kwargs.get("params", {})
    assert params.get("$expand") == "attachments"


def test_get_message_without_expand():
    graph = MagicMock()
    graph.get.return_value = _msg_raw("m1")
    m = get_message(graph, mailbox_spec="me", auth_mode="delegated", message_id="m1")
    assert m.id == "m1"
    # When with_attachments is False, we don't pass $expand.
    kwargs = graph.get.call_args.kwargs
    params = kwargs.get("params")
    assert params is None or "$expand" not in params


def test_search_messages_graph_posts_query():
    graph = MagicMock()
    graph.post.return_value = {
        "value": [{
            "hitsContainers": [{
                "hits": [
                    {"resource": _msg_raw("hit1")},
                    {"resource": _msg_raw("hit2")},
                ]
            }]
        }]
    }
    out = list(search_messages_graph(graph, query="invoice", limit=25))
    assert len(out) == 2
    assert out[0].id == "hit1"
    assert graph.post.call_args.args[0] == "/search/query"
    payload = graph.post.call_args.kwargs["json"]
    assert payload["requests"][0]["entityTypes"] == ["message"]
    assert payload["requests"][0]["query"]["queryString"] == "invoice"
    assert payload["requests"][0]["size"] == 25


def test_search_messages_graph_skips_hits_without_resource():
    graph = MagicMock()
    graph.post.return_value = {
        "value": [{
            "hitsContainers": [{
                "hits": [
                    {"resource": _msg_raw("h1")},
                    {},  # malformed — no resource
                    {"resource": _msg_raw("h2")},
                ]
            }]
        }]
    }
    out = list(search_messages_graph(graph, query="x", limit=5))
    assert [m.id for m in out] == ["h1", "h2"]


def test_get_thread_walks_conversation_id():
    graph = _graph_with_single_page([{
        "value": [_msg_raw("m1"), _msg_raw("m2"), _msg_raw("m3")],
    }])
    out = list(get_thread(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        conversation_id="conv-m1",
        parent_folder_path="/Inbox",
    ))
    assert [m.id for m in out] == ["m1", "m2", "m3"]
    url = graph.get_paginated.call_args.args[0]
    assert url == "/me/messages"
    params = graph.get_paginated.call_args.kwargs["params"]
    assert "conversationId eq 'conv-m1'" in params["$filter"]
    assert params["$orderby"] == "receivedDateTime asc"


def _msg_raw_at(msg_id: str, received: str, subject: str = "") -> dict:
    raw = _msg_raw(msg_id)
    raw["receivedDateTime"] = received
    if subject:
        raw["subject"] = subject
    return raw


def test_list_messages_falls_back_on_inefficient_filter(capsys):
    """When Graph rejects $filter+$orderby with InefficientFilter, retry without
    them and apply the predicates client-side, sorting by receivedDateTime desc.
    """
    raw_pages = [
        # Mixed-ordered, mostly non-matching, with a few matching subjects.
        {"value": [
            _msg_raw_at("a1", "2026-04-20T08:00:00Z", "no match"),
            _msg_raw_at("a2", "2026-04-21T08:00:00Z", "m365ctl smoke run 1"),
            _msg_raw_at("a3", "2026-04-19T08:00:00Z", "M365CTL SMOKE shouty"),
            _msg_raw_at("a4", "2026-04-22T08:00:00Z", "totally unrelated"),
            _msg_raw_at("a5", "2026-04-23T08:00:00Z", "preamble m365ctl smoke trailing"),
            _msg_raw_at("a6", "2026-04-18T08:00:00Z", "m365ctl smoke older"),
            _msg_raw_at("a7", "2026-04-24T08:00:00Z", "newest m365ctl smoke"),
            _msg_raw_at("a8", "2026-04-17T08:00:00Z", "m365ctl smoke oldest"),
            _msg_raw_at("a9", "2026-04-15T08:00:00Z", "noise"),
        ]},
    ]

    calls: list[dict] = []

    def fake_get_paginated(path, *, params=None, headers=None):
        calls.append({"path": path, "params": dict(params or {})})
        if len(calls) == 1:
            # First call: should include $filter — raise InefficientFilter
            raise GraphError(
                "BadRequest:InefficientFilter: The restriction or sort order "
                "is too complex for this operation."
            )
        # Second call: should be without $filter / $orderby
        def gen():
            for p in raw_pages:
                yield (p["value"], None)
        return gen()

    graph = MagicMock()
    graph.get_paginated.side_effect = fake_get_paginated

    out = list(list_messages(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        folder_id="AAMkAD..inbox",
        parent_folder_path="/Inbox",
        filters=MessageListFilters(subject_contains="m365ctl smoke"),
        limit=5,
    ))

    # Limit honoured.
    assert len(out) == 5
    # All matched the subject_contains predicate (case-insensitive substring).
    assert all("m365ctl smoke" in m.subject.lower() for m in out)
    # Sorted by receivedDateTime descending.
    received = [m.received_at for m in out]
    assert received == sorted(received, reverse=True)

    # Two calls: first with $filter+$orderby, second without.
    assert len(calls) == 2
    first_params = calls[0]["params"]
    assert "$filter" in first_params
    assert "$orderby" in first_params
    second_params = calls[1]["params"]
    assert "$filter" not in second_params
    assert "$orderby" not in second_params

    # Stderr notice printed once.
    err = capsys.readouterr().err
    assert err.count(
        "[mail list] Graph rejected $filter as inefficient; "
        "retrying with client-side filtering."
    ) == 1


def test_list_messages_does_not_swallow_other_graph_errors():
    graph = MagicMock()
    graph.get_paginated.side_effect = GraphError("Forbidden: nope")
    with pytest.raises(GraphError, match="Forbidden"):
        list(list_messages(
            graph,
            mailbox_spec="me",
            auth_mode="delegated",
            folder_id="inbox",
            parent_folder_path="/Inbox",
            filters=MessageListFilters(subject_contains="anything"),
        ))


def test_list_messages_fallback_applies_all_predicates_client_side():
    """The fallback path must apply every filter clause, not just subject."""
    pages = [{"value": [
        # Should not match: read.
        {**_msg_raw_at("r1", "2026-04-22T10:00:00Z", "hello world"), "isRead": True},
        # Should match: unread + subject contains + has_attachments + category.
        {
            **_msg_raw_at("r2", "2026-04-23T10:00:00Z", "Hello WORLD"),
            "isRead": False,
            "hasAttachments": True,
            "categories": ["Followup"],
        },
        # Should not match: missing category.
        {
            **_msg_raw_at("r3", "2026-04-24T10:00:00Z", "hello world"),
            "isRead": False,
            "hasAttachments": True,
            "categories": [],
        },
    ]}]

    def fake_get_paginated(path, *, params=None, headers=None):
        if "$filter" in (params or {}):
            raise GraphError("InefficientFilter: too complex")
        def gen():
            for p in pages:
                yield (p["value"], None)
        return gen()

    graph = MagicMock()
    graph.get_paginated.side_effect = fake_get_paginated

    out = list(list_messages(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        folder_id="inbox",
        parent_folder_path="/Inbox",
        filters=MessageListFilters(
            unread=True,
            subject_contains="hello",
            has_attachments=True,
            category="Followup",
        ),
    ))
    assert [m.id for m in out] == ["r2"]


# ----------------------------------------------------------------------------
# find_by_internet_message_id — Phase 4.x soft-delete undo recovery helper.
# Graph rotates a message's literal id when it crosses folder boundaries
# (e.g. on move-to-Deleted-Items). The RFC-822 internetMessageId is preserved,
# so we use it to locate the rotated message in Deleted Items at undo time.
# ----------------------------------------------------------------------------

def test_find_by_internet_message_id_returns_rotated_id_on_hit():
    graph = MagicMock()
    graph.get.return_value = {"value": [{"id": "rotated-id-1"}]}
    out = find_by_internet_message_id(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        folder_id="deleteditems",
        internet_message_id="<abc@example.com>",
    )
    assert out == "rotated-id-1"
    call_args = graph.get.call_args
    assert call_args.args[0] == "/me/mailFolders/deleteditems/messages"
    params = call_args.kwargs["params"]
    assert params["$filter"] == "internetMessageId eq '<abc@example.com>'"
    assert params["$top"] == 1
    assert params["$select"] == "id"


def test_find_by_internet_message_id_returns_none_when_no_hit():
    graph = MagicMock()
    graph.get.return_value = {"value": []}
    out = find_by_internet_message_id(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        folder_id="deleteditems",
        internet_message_id="<abc@example.com>",
    )
    assert out is None


def test_find_by_internet_message_id_app_only_routes_via_users_upn():
    graph = MagicMock()
    graph.get.return_value = {"value": [{"id": "rotated-id-2"}]}
    find_by_internet_message_id(
        graph,
        mailbox_spec="upn:bob@example.com",
        auth_mode="app-only",
        folder_id="deleteditems",
        internet_message_id="<abc@example.com>",
    )
    url = graph.get.call_args.args[0]
    assert url == "/users/bob@example.com/mailFolders/deleteditems/messages"


def test_find_by_internet_message_id_escapes_single_quote():
    graph = MagicMock()
    graph.get.return_value = {"value": []}
    find_by_internet_message_id(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        folder_id="deleteditems",
        internet_message_id="<O'Brien@example.com>",
    )
    params = graph.get.call_args.kwargs["params"]
    assert "''" in params["$filter"]
    assert params["$filter"] == "internetMessageId eq '<O''Brien@example.com>'"
