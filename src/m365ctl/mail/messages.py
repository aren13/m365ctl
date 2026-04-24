"""Read-only message operations over Microsoft Graph.

All functions take a ``GraphClient`` and return ``Message`` dataclasses
(or iterators thereof). Pagination is handled via ``GraphClient.get_paginated``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.models import Message


@dataclass(frozen=True)
class MessageListFilters:
    """OData $filter inputs for ``list_messages``.

    Each field maps to a single ``$filter`` clause; clauses are ANDed.
    Leave any field at its default to omit the clause.
    """
    unread: bool | None = None
    from_address: str | None = None
    subject_contains: str | None = None
    since: str | None = None
    until: str | None = None
    has_attachments: bool | None = None
    importance: str | None = None
    focus: str | None = None
    category: str | None = None


def _build_filter_expr(f: MessageListFilters) -> str:
    clauses: list[str] = []
    if f.unread is True:
        clauses.append("isRead eq false")
    elif f.unread is False:
        clauses.append("isRead eq true")
    if f.from_address:
        clauses.append(f"from/emailAddress/address eq '{f.from_address}'")
    if f.subject_contains:
        esc = f.subject_contains.replace("'", "''")
        clauses.append(f"contains(subject, '{esc}')")
    if f.since:
        clauses.append(f"receivedDateTime ge {f.since}")
    if f.until:
        clauses.append(f"receivedDateTime le {f.until}")
    if f.has_attachments is True:
        clauses.append("hasAttachments eq true")
    elif f.has_attachments is False:
        clauses.append("hasAttachments eq false")
    if f.importance:
        clauses.append(f"importance eq '{f.importance}'")
    if f.focus:
        clauses.append(f"inferenceClassification eq '{f.focus}'")
    if f.category:
        esc = f.category.replace("'", "''")
        clauses.append(f"categories/any(c:c eq '{esc}')")
    return " and ".join(clauses)


def _derive_mailbox_upn(mailbox_spec: str) -> str:
    """Return the address-or-keyword for Message.mailbox_upn."""
    if mailbox_spec == "me":
        return "me"
    if mailbox_spec.startswith("upn:"):
        return mailbox_spec[len("upn:"):]
    if mailbox_spec.startswith("shared:"):
        return mailbox_spec[len("shared:"):]
    return mailbox_spec


def list_messages(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    folder_id: str,
    parent_folder_path: str,
    filters: MessageListFilters | None = None,
    limit: int | None = None,
    page_size: int = 50,
) -> Iterator[Message]:
    """Yield messages from ``folder_id``, optionally filtered."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    path = f"{ub}/mailFolders/{folder_id}/messages"

    filters = filters or MessageListFilters()
    filter_expr = _build_filter_expr(filters)

    params: dict = {
        "$orderby": "receivedDateTime desc",
        "$top": limit if limit is not None else page_size,
    }
    if filter_expr:
        params["$filter"] = filter_expr

    mailbox_upn = _derive_mailbox_upn(mailbox_spec)
    count = 0
    for items, _ in graph.get_paginated(path, params=params):
        for raw in items:
            yield Message.from_graph_json(
                raw, mailbox_upn=mailbox_upn, parent_folder_path=parent_folder_path,
            )
            count += 1
            if limit is not None and count >= limit:
                return


def get_message(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    message_id: str,
    with_attachments: bool = False,
    parent_folder_path: str = "",
) -> Message:
    """Fetch a single message by id. ``with_attachments=True`` $expands the attachments."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    path = f"{ub}/messages/{message_id}"
    params: dict | None = None
    if with_attachments:
        params = {"$expand": "attachments"}
    raw = graph.get(path, params=params)
    mailbox_upn = _derive_mailbox_upn(mailbox_spec)
    return Message.from_graph_json(
        raw, mailbox_upn=mailbox_upn, parent_folder_path=parent_folder_path,
    )


def search_messages_graph(
    graph: GraphClient,
    *,
    query: str,
    limit: int = 25,
) -> Iterator[Message]:
    """Server-side /search/query across all mail folders the caller can see."""
    payload = {
        "requests": [
            {
                "entityTypes": ["message"],
                "query": {"queryString": query},
                "from": 0,
                "size": limit,
            }
        ]
    }
    resp = graph.post("/search/query", json=payload)
    for response in resp.get("value", []):
        for container in response.get("hitsContainers", []):
            for hit in container.get("hits", []):
                raw = hit.get("resource")
                if not raw:
                    continue
                # Search responses sometimes omit ``id`` from the embedded
                # resource (Graph returns a hit-level ``hitId`` instead).
                # Backfill to keep Message.from_graph_json's ``raw["id"]``
                # contract happy. Skip if neither is available.
                if "id" not in raw:
                    hit_id = hit.get("hitId")
                    if not hit_id:
                        continue
                    raw = {**raw, "id": hit_id}
                # ``receivedDateTime`` may also be missing on partial hits;
                # fall back to ``sentDateTime`` (Message requires received).
                if "receivedDateTime" not in raw and "sentDateTime" in raw:
                    raw = {**raw, "receivedDateTime": raw["sentDateTime"]}
                if "receivedDateTime" not in raw:
                    continue
                yield Message.from_graph_json(
                    raw,
                    mailbox_upn=_derive_mailbox_upn("me"),
                    parent_folder_path="",
                )


def get_thread(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    conversation_id: str,
    parent_folder_path: str = "",
) -> Iterator[Message]:
    """Walk all messages in ``conversation_id`` chronologically (oldest first)."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    path = f"{ub}/messages"
    params = {
        "$filter": f"conversationId eq '{conversation_id}'",
        "$orderby": "receivedDateTime asc",
        "$top": 200,
    }
    mailbox_upn = _derive_mailbox_upn(mailbox_spec)
    for items, _ in graph.get_paginated(path, params=params):
        for raw in items:
            yield Message.from_graph_json(
                raw, mailbox_upn=mailbox_upn, parent_folder_path=parent_folder_path,
            )
