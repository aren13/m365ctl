"""Tests for m365ctl.mail.messages — readers over a mocked Graph client."""
from __future__ import annotations

from unittest.mock import MagicMock


from m365ctl.mail.messages import (
    MessageListFilters,
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
