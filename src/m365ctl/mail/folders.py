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

from dataclasses import dataclass
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
    #
    # Graph's default /mailFolders listing does NOT include the
    # ``wellKnownName`` field — even on the well-known folders themselves
    # (verified live, 2026-04-25). So we cannot iterate list_folders and
    # match on ``well_known_name``. Instead, hit the well-known endpoint
    # directly: Graph accepts ``inbox``/``drafts``/``sentitems``/etc. as
    # valid folder identifiers under /mailFolders/{id}.
    if path.strip("/").lower() in _WELL_KNOWN_NAMES:
        wk = path.strip("/").lower()
        ub = user_base(mailbox_spec, auth_mode=auth_mode)
        try:
            raw = graph.get(f"{ub}/mailFolders/{wk}")
        except Exception as exc:
            raise FolderNotFound(
                f"well-known folder {path!r} not found in mailbox"
            ) from exc
        return raw["id"]

    # Explicit path.
    target = path.strip("/").lower()
    for folder in list_folders(graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode):
        if folder.path.lower() == target:
            return folder.id
    raise FolderNotFound(f"folder path {path!r} not found in mailbox")


def resolve_folder_paths(
    paths: list[str],
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
) -> dict[str, str]:
    """Resolve many folder paths in one shot, batching per-tier child lookups.

    Returns a dict ``{original_path: folder_id}`` for paths that resolved.
    Paths that do not resolve are simply omitted from the returned dict —
    callers that need a hard error can check ``len(out) == len(paths)`` and
    fall back to the single-path ``resolve_folder_path`` for the missing ones
    (which raises ``FolderNotFound``).

    Behavior parity with ``resolve_folder_path``:
    - Case-insensitive matching.
    - Well-known names (``inbox``, ``drafts``, etc.) hit the well-known
      endpoint directly, batched together.
    - Leading/trailing slashes are tolerated.

    Performance: one ``/$batch`` POST per depth tier (well-known fast-path
    GETs counted as one tier), so resolving e.g. 5 paths each 3 segments
    deep costs ~3 batched POSTs instead of 5 sequential ``list_folders``
    walks.
    """
    if not paths:
        return {}

    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    out: dict[str, str] = {}

    # Tier 0a: batch the well-known fast-path GETs.
    well_known_targets: list[tuple[str, str]] = []  # (orig, wk_name)
    explicit: list[tuple[str, list[str]]] = []  # (orig, [seg_lower, ...])
    for p in paths:
        stripped = p.strip("/")
        low = stripped.lower()
        if low in _WELL_KNOWN_NAMES:
            well_known_targets.append((p, low))
        else:
            segs = [s for s in stripped.lower().split("/") if s]
            if segs:
                explicit.append((p, segs))

    if well_known_targets:
        with graph.batch() as b:
            futs = [(orig, b.get(f"{ub}/mailFolders/{wk}"))
                    for orig, wk in well_known_targets]
        for orig, fut in futs:
            try:
                body = fut.result()
            except Exception:
                continue
            fid = body.get("id") if isinstance(body, dict) else None
            if isinstance(fid, str) and fid:
                out[orig] = fid

    if not explicit:
        return out

    # Tier 0b: list root /mailFolders (shared). One un-batched GET shared
    # by all explicit paths.
    root_index: dict[str, str] = {}  # name_lower -> folder_id
    for items, _ in graph.get_paginated(f"{ub}/mailFolders", params={"$top": 200}):
        for raw in items:
            disp = (raw.get("displayName") or "").lower()
            fid = raw.get("id")
            if disp and isinstance(fid, str):
                root_index[disp] = fid

    # Build per-path traversal state.
    #
    # state: list of dicts with original-path, remaining-segments, current-id.
    # A path with N segments needs N-1 batched tier expansions after the
    # tier-0 root resolution.
    @dataclass
    class _Pending:
        orig: str
        remaining: list[str]
        current_id: str

    pending: list[_Pending] = []
    for orig, segs in explicit:
        first = segs[0]
        fid = root_index.get(first)
        if not fid:
            continue
        if len(segs) == 1:
            out[orig] = fid
            continue
        pending.append(_Pending(orig=orig, remaining=segs[1:], current_id=fid))

    # Tier N: for each remaining segment, batch GETs of childFolders for
    # every still-pending path's current_id. After parsing the response,
    # advance each path; drop paths that miss.
    while pending:
        with graph.batch() as b:
            shots = [
                (p, b.get(f"{ub}/mailFolders/{p.current_id}/childFolders?$top=200"))
                for p in pending
            ]
        next_pending: list[_Pending] = []
        for p, fut in shots:
            try:
                body = fut.result()
            except Exception:
                continue
            children = body.get("value", []) if isinstance(body, dict) else []
            target = p.remaining[0]
            match_id: str | None = None
            for raw in children:
                if (raw.get("displayName") or "").lower() == target:
                    cid = raw.get("id")
                    if isinstance(cid, str):
                        match_id = cid
                        break
            if match_id is None:
                continue
            rest = p.remaining[1:]
            if not rest:
                out[p.orig] = match_id
            else:
                next_pending.append(_Pending(
                    orig=p.orig, remaining=rest, current_id=match_id,
                ))
        pending = next_pending

    return out


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
