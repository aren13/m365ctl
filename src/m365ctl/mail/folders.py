"""Read-only folder operations.

Folder listing is recursive by default — the caller gets a flat iterable
of ``Folder`` dataclasses with ``path`` already resolved (e.g. ``"Inbox/Triage"``).
Depth-first traversal; children are requested via ``/mailFolders/{id}/childFolders``
only when ``childFolderCount > 0``.

``resolve_folder_path`` is case-insensitive and accepts well-known names
(``inbox``, ``drafts``, ``sentitems``, ``deleteditems``, ``junkemail``,
``outbox``, ``archive``) directly.
"""
from __future__ import annotations

from typing import Iterator

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.models import Folder


class FolderNotFound(LookupError):
    """Raised when ``resolve_folder_path`` can't find a folder."""


def _derive_mailbox_upn(mailbox_spec: str) -> str:
    if mailbox_spec == "me":
        return "me"
    if mailbox_spec.startswith("upn:") or mailbox_spec.startswith("shared:"):
        return mailbox_spec.split(":", 1)[1]
    return mailbox_spec


def _walk(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    parent_id: str | None,
    parent_path: str,
    include_hidden: bool,
) -> Iterator[Folder]:
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    if parent_id is None:
        path = f"{ub}/mailFolders"
    else:
        path = f"{ub}/mailFolders/{parent_id}/childFolders"
    params: dict = {"$top": 200}
    if include_hidden:
        params["includeHiddenFolders"] = "true"

    mailbox_upn = _derive_mailbox_upn(mailbox_spec)
    for items, _ in graph.get_paginated(path, params=params):
        for raw in items:
            disp = raw.get("displayName", "")
            child_path = f"{parent_path}/{disp}" if parent_path else disp
            folder = Folder.from_graph_json(raw, mailbox_upn=mailbox_upn, path=child_path)
            yield folder
            if raw.get("childFolderCount", 0) > 0:
                yield from _walk(
                    graph,
                    mailbox_spec=mailbox_spec,
                    auth_mode=auth_mode,
                    parent_id=raw["id"],
                    parent_path=child_path,
                    include_hidden=include_hidden,
                )


def list_folders(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    include_hidden: bool = False,
) -> Iterator[Folder]:
    """Yield every folder in the mailbox as a flat iterable."""
    yield from _walk(
        graph,
        mailbox_spec=mailbox_spec,
        auth_mode=auth_mode,
        parent_id=None,
        parent_path="",
        include_hidden=include_hidden,
    )


_WELL_KNOWN_NAMES = frozenset({
    "inbox", "drafts", "sentitems", "deleteditems", "junkemail",
    "outbox", "archive",
})


def resolve_folder_path(
    path: str,
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
) -> str:
    """Translate a path like ``"Inbox/Triage"`` to a folder id.

    Also accepts well-known names like ``"inbox"`` (case-insensitive).
    Leading slashes on paths are tolerated (``"/Inbox"`` works).
    """
    # Well-known name fast path.
    if path.strip("/").lower() in _WELL_KNOWN_NAMES:
        needle = path.strip("/").lower()
        for folder in list_folders(graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode):
            if (folder.well_known_name or "").lower() == needle:
                return folder.id
        raise FolderNotFound(f"well-known folder {path!r} not found in mailbox")

    # Explicit path.
    target = path.strip("/").lower()
    for folder in list_folders(graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode):
        if folder.path.lower() == target:
            return folder.id
    raise FolderNotFound(f"folder path {path!r} not found in mailbox")


def get_folder(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    folder_id: str,
    path: str,
) -> Folder:
    """Fetch a single folder by id."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    raw = graph.get(f"{ub}/mailFolders/{folder_id}")
    mailbox_upn = _derive_mailbox_upn(mailbox_spec)
    return Folder.from_graph_json(raw, mailbox_upn=mailbox_upn, path=path)
