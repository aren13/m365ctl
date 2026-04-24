"""Per-folder ``/messages/delta`` crawler for the mail catalog.

Resumes from stored ``delta_link`` when present. On Graph
``syncStateNotFound`` (HTTP 410), drops the stored link and full-resyncs
the folder, marking ``last_status='restarted'``.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from m365ctl.common.config import AuthMode
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.mail.catalog.normalize import normalize_folder, normalize_message
from m365ctl.mail.endpoints import user_base
from m365ctl.mail.folders import list_folders


_DEFAULT_WELL_KNOWN: tuple[str, ...] = (
    "inbox", "sentitems", "drafts", "deleteditems",
)


@dataclass(frozen=True)
class CrawlOutcome:
    folder_id: str
    folder_path: str
    messages_seen: int
    delta_link: str | None
    status: str   # 'ok' | 'restarted'


_UPSERT_FOLDER = """
INSERT INTO mail_folders (
    mailbox_upn, folder_id, display_name, parent_folder_id, path,
    well_known_name, total_items, unread_items, child_folder_count, last_seen_at
) VALUES (
    $mailbox_upn, $folder_id, $display_name, $parent_folder_id, $path,
    $well_known_name, $total_items, $unread_items, $child_folder_count, $last_seen_at
)
ON CONFLICT (mailbox_upn, folder_id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    parent_folder_id = EXCLUDED.parent_folder_id,
    path = EXCLUDED.path,
    well_known_name = EXCLUDED.well_known_name,
    total_items = EXCLUDED.total_items,
    unread_items = EXCLUDED.unread_items,
    child_folder_count = EXCLUDED.child_folder_count,
    last_seen_at = EXCLUDED.last_seen_at
"""

_UPSERT_MESSAGE = """
INSERT INTO mail_messages (
    mailbox_upn, message_id, internet_message_id, conversation_id,
    parent_folder_id, parent_folder_path, subject, from_address, from_name,
    to_addresses, received_at, sent_at, is_read, is_draft, has_attachments,
    importance, flag_status, categories, inference_class, body_preview,
    web_link, size_estimate, is_deleted, last_seen_at
) VALUES (
    $mailbox_upn, $message_id, $internet_message_id, $conversation_id,
    $parent_folder_id, $parent_folder_path, $subject, $from_address, $from_name,
    $to_addresses, $received_at, $sent_at, $is_read, $is_draft, $has_attachments,
    $importance, $flag_status, $categories, $inference_class, $body_preview,
    $web_link, $size_estimate, $is_deleted, $last_seen_at
)
ON CONFLICT (mailbox_upn, message_id) DO UPDATE SET
    internet_message_id = EXCLUDED.internet_message_id,
    conversation_id = EXCLUDED.conversation_id,
    parent_folder_id = EXCLUDED.parent_folder_id,
    parent_folder_path = EXCLUDED.parent_folder_path,
    subject = EXCLUDED.subject,
    from_address = EXCLUDED.from_address,
    from_name = EXCLUDED.from_name,
    to_addresses = EXCLUDED.to_addresses,
    received_at = EXCLUDED.received_at,
    sent_at = EXCLUDED.sent_at,
    is_read = EXCLUDED.is_read,
    is_draft = EXCLUDED.is_draft,
    has_attachments = EXCLUDED.has_attachments,
    importance = EXCLUDED.importance,
    flag_status = EXCLUDED.flag_status,
    categories = EXCLUDED.categories,
    inference_class = EXCLUDED.inference_class,
    body_preview = EXCLUDED.body_preview,
    web_link = EXCLUDED.web_link,
    size_estimate = EXCLUDED.size_estimate,
    is_deleted = EXCLUDED.is_deleted,
    last_seen_at = EXCLUDED.last_seen_at
"""

_UPSERT_DELTA = """
INSERT INTO mail_deltas (
    mailbox_upn, folder_id, delta_link, last_refreshed_at, last_status
) VALUES (?, ?, ?, ?, ?)
ON CONFLICT (mailbox_upn, folder_id) DO UPDATE SET
    delta_link = EXCLUDED.delta_link,
    last_refreshed_at = EXCLUDED.last_refreshed_at,
    last_status = EXCLUDED.last_status
"""


def _is_sync_state_not_found(exc: GraphError) -> bool:
    # GraphError messages are formatted "<code>: <msg>" by GraphClient._parse,
    # so the prefix is either "syncStateNotFound:" or "HTTP410:" depending on
    # whether Graph returned a typed error code. Anchor on those tokens so
    # arbitrary messages containing "410" elsewhere don't trigger a resync.
    msg = str(exc).lower()
    return "syncstatenotfound" in msg or "http410" in msg


def _stored_delta_link(conn, *, mailbox_upn: str, folder_id: str) -> str | None:
    row = conn.execute(
        "SELECT delta_link FROM mail_deltas "
        "WHERE mailbox_upn = ? AND folder_id = ?",
        [mailbox_upn, folder_id],
    ).fetchone()
    return row[0] if row else None


def _drain_delta(
    graph: GraphClient,
    conn,
    *,
    mailbox_upn: str,
    folder_id: str,
    folder_path: str,
    start_path: str,
    page_top: int,
) -> tuple[int, str | None]:
    """Drain ``/messages/delta`` until a deltaLink-only round returns no items.

    Graph's mail delta works in **rounds** — each round ends with a
    ``deltaLink`` rather than draining the entire mailbox in one chain of
    nextLinks. To do a full first-time sync we have to follow each
    deltaLink immediately and keep going until a round comes back empty.

    Page size is set via ``Prefer: odata.maxpagesize=N`` because Graph's
    ``/messages/delta`` ignores ``$top`` and falls back to its 10-item
    default (verified live, 2026-04-25).
    """
    seen = 0
    cursor = start_path
    last_delta: str | None = None
    headers = {"Prefer": f"odata.maxpagesize={page_top}"}
    while True:
        round_items = 0
        round_delta: str | None = None
        if cursor.startswith("http"):
            pages = graph.get_paginated(cursor, headers=headers)
        else:
            pages = graph.get_paginated(cursor, headers=headers)
        for items, delta_link in pages:
            for raw in items:
                row = normalize_message(mailbox_upn, raw, parent_folder_path=folder_path)
                if row.get("parent_folder_id") is None:
                    row["parent_folder_id"] = folder_id
                conn.execute(_UPSERT_MESSAGE, row)
                seen += 1
                round_items += 1
            if delta_link:
                round_delta = delta_link
        if round_delta:
            last_delta = round_delta
        # Stop when a round closed with a deltaLink and produced no new items.
        if round_delta is not None and round_items == 0:
            break
        # If the round had items but no deltaLink, the iterator already
        # exhausted nextLinks; bail out (shouldn't happen with delta).
        if round_delta is None:
            break
        cursor = round_delta
    return seen, last_delta


def crawl_folder(
    graph: GraphClient,
    *,
    conn,
    mailbox_upn: str,
    folder_id: str,
    folder_path: str,
    initial_path: str,
    page_top: int = 200,
) -> CrawlOutcome:
    stored = _stored_delta_link(conn, mailbox_upn=mailbox_upn, folder_id=folder_id)
    start_path = stored or initial_path
    status = "ok"
    try:
        seen, delta_link = _drain_delta(
            graph, conn,
            mailbox_upn=mailbox_upn, folder_id=folder_id,
            folder_path=folder_path, start_path=start_path, page_top=page_top,
        )
    except GraphError as exc:
        if not _is_sync_state_not_found(exc):
            raise
        print(
            f"[mail-catalog] delta token expired for {folder_path!r}; "
            f"restarting full sync...",
            file=sys.stderr,
        )
        status = "restarted"
        seen, delta_link = _drain_delta(
            graph, conn,
            mailbox_upn=mailbox_upn, folder_id=folder_id,
            folder_path=folder_path, start_path=initial_path, page_top=page_top,
        )

    final_link = delta_link or stored
    conn.execute(
        _UPSERT_DELTA,
        [mailbox_upn, folder_id, final_link, datetime.now(timezone.utc), status],
    )
    return CrawlOutcome(
        folder_id=folder_id,
        folder_path=folder_path,
        messages_seen=seen,
        delta_link=final_link,
        status=status,
    )


def refresh_mailbox(
    graph: GraphClient,
    *,
    conn,
    mailbox_spec: str,
    mailbox_upn: str,
    auth_mode: AuthMode,
    folder_filter: str | None = None,
    page_top: int = 200,
) -> list[CrawlOutcome]:
    """High-level orchestrator.

    1. List all folders (recursive) and upsert into ``mail_folders``.
    2. Pick targets: ``folder_filter`` (folder id) if given, else all
       well-known folders that exist in this mailbox.
    3. Crawl each target via ``crawl_folder``.
    """
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    seen_folders: list[dict] = []
    for folder in list_folders(
        graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
    ):
        row = normalize_folder(
            mailbox_upn,
            {
                "id": folder.id,
                "displayName": folder.display_name,
                "parentFolderId": folder.parent_id,
                "wellKnownName": folder.well_known_name,
                "totalItemCount": folder.total_items,
                "unreadItemCount": folder.unread_items,
                "childFolderCount": folder.child_folder_count,
            },
            path=folder.path,
        )
        conn.execute(_UPSERT_FOLDER, row)
        seen_folders.append({
            "id": folder.id,
            "path": folder.path,
            "well_known_name": folder.well_known_name,
        })

    if folder_filter is not None:
        targets = [f for f in seen_folders if f["id"] == folder_filter]
    else:
        # Graph's /mailFolders listing does NOT return wellKnownName, so we
        # can't filter ``seen_folders`` by ``well_known_name``. Resolve each
        # well-known name to its id by hitting /mailFolders/{wk} directly,
        # then map back to the upserted folder row for the path.
        seen_by_id = {f["id"]: f for f in seen_folders}
        targets = []
        for wk in _DEFAULT_WELL_KNOWN:
            try:
                raw = graph.get(f"{ub}/mailFolders/{wk}")
            except GraphError:
                continue  # mailbox doesn't have this well-known folder
            row = seen_by_id.get(raw["id"])
            if row is not None:
                targets.append(row)
            else:
                # Listing missed it (hidden / regional quirk) — synthesise.
                targets.append({
                    "id": raw["id"],
                    "path": raw.get("displayName", wk),
                    "well_known_name": wk,
                })

    outcomes: list[CrawlOutcome] = []
    for f in targets:
        outcomes.append(
            crawl_folder(
                graph,
                conn=conn,
                mailbox_upn=mailbox_upn,
                folder_id=f["id"],
                folder_path=f["path"],
                initial_path=f"{ub}/mailFolders/{f['id']}/messages/delta",
                page_top=page_top,
            )
        )
    return outcomes
