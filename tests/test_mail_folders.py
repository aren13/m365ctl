"""Tests for m365ctl.mail.folders — recursive folder walk + path resolution."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from m365ctl.mail.folders import (
    FolderNotFound,
    get_folder,
    list_folders,
    resolve_folder_path,
)
from m365ctl.mail.models import Folder


def _folder_raw(fid: str, name: str, parent: str | None = None, well_known: str | None = None,
                child_count: int = 0, total: int = 0, unread: int = 0) -> dict:
    return {
        "id": fid,
        "displayName": name,
        "parentFolderId": parent,
        "childFolderCount": child_count,
        "totalItemCount": total,
        "unreadItemCount": unread,
        "wellKnownName": well_known,
    }


def _graph_flat_tree(root_children: list[dict], sub_map: dict[str, list[dict]] | None = None) -> MagicMock:
    """Return a MagicMock GraphClient whose get_paginated returns root children,
    then expand sub-folders per id from sub_map on successive calls."""
    graph = MagicMock()
    sub_map = sub_map or {}

    def _paginated(path, params=None):
        if path.endswith("/mailFolders"):
            return iter([(root_children, None)])
        for fid, kids in sub_map.items():
            if path.endswith(f"/mailFolders/{fid}/childFolders"):
                return iter([(kids, None)])
        return iter([([], None)])

    graph.get_paginated.side_effect = _paginated
    return graph


def test_list_folders_flat_root():
    graph = _graph_flat_tree([
        _folder_raw("f1", "Inbox", well_known="inbox"),
        _folder_raw("f2", "Sent Items", well_known="sentitems"),
    ])
    out = list(list_folders(graph, mailbox_spec="me", auth_mode="delegated"))
    names = [(f.id, f.path) for f in out]
    assert ("f1", "Inbox") in names
    assert ("f2", "Sent Items") in names


def test_list_folders_recurses_children():
    graph = _graph_flat_tree(
        root_children=[_folder_raw("inbox", "Inbox", child_count=1, well_known="inbox")],
        sub_map={
            "inbox": [_folder_raw("triage", "Triage", parent="inbox", child_count=1)],
            "triage": [_folder_raw("waiting", "Waiting", parent="triage")],
        },
    )
    out = list(list_folders(graph, mailbox_spec="me", auth_mode="delegated"))
    paths = [f.path for f in out]
    assert "Inbox" in paths
    assert "Inbox/Triage" in paths
    assert "Inbox/Triage/Waiting" in paths
    assert len(out) == 3


def test_list_folders_include_hidden_flag():
    graph = _graph_flat_tree([_folder_raw("f1", "Inbox")])
    list(list_folders(graph, mailbox_spec="me", auth_mode="delegated", include_hidden=True))
    params = graph.get_paginated.call_args.kwargs["params"]
    assert params.get("includeHiddenFolders") == "true"


def test_list_folders_app_only_routes_via_users_upn():
    graph = _graph_flat_tree([_folder_raw("f1", "Inbox")])
    list(list_folders(graph, mailbox_spec="upn:bob@example.com", auth_mode="app-only"))
    # First call goes to /users/bob@example.com/mailFolders.
    first_call_path = graph.get_paginated.call_args_list[0].args[0]
    assert first_call_path == "/users/bob@example.com/mailFolders"


def test_resolve_folder_path_hits_nested_tree():
    graph = _graph_flat_tree(
        root_children=[_folder_raw("inbox", "Inbox", child_count=1, well_known="inbox")],
        sub_map={"inbox": [_folder_raw("triage", "Triage", parent="inbox")]},
    )
    fid = resolve_folder_path("Inbox/Triage", graph, mailbox_spec="me", auth_mode="delegated")
    assert fid == "triage"


def test_resolve_folder_path_case_insensitive_match():
    # Well-known names hit Graph's /mailFolders/{wellKnownName} endpoint
    # directly — Graph's listing doesn't return wellKnownName so the
    # iteration approach can't work (verified live 2026-04-25).
    graph = MagicMock()
    graph.get.return_value = {"id": "f1", "displayName": "Inbox"}
    fid = resolve_folder_path("inbox", graph, mailbox_spec="me", auth_mode="delegated")
    assert fid == "f1"
    graph.get.assert_called_once_with("/me/mailFolders/inbox")


def test_resolve_folder_path_resolves_well_known_names():
    graph = MagicMock()
    graph.get.side_effect = [
        {"id": "f1", "displayName": "Inbox"},
        {"id": "f2", "displayName": "Sent Items"},
    ]
    assert resolve_folder_path("inbox", graph, mailbox_spec="me", auth_mode="delegated") == "f1"
    assert resolve_folder_path("sentitems", graph, mailbox_spec="me", auth_mode="delegated") == "f2"


def test_resolve_folder_path_missing_raises():
    graph = _graph_flat_tree([_folder_raw("inbox", "Inbox", well_known="inbox")])
    with pytest.raises(FolderNotFound):
        resolve_folder_path("NonExistent", graph, mailbox_spec="me", auth_mode="delegated")


def test_resolve_folder_path_leading_slash_tolerated():
    graph = MagicMock()
    graph.get.return_value = {"id": "inbox", "displayName": "Inbox"}
    fid = resolve_folder_path("/Inbox", graph, mailbox_spec="me", auth_mode="delegated")
    assert fid == "inbox"


def test_get_folder_by_id():
    graph = MagicMock()
    graph.get.return_value = _folder_raw("f1", "Inbox", well_known="inbox")
    f = get_folder(graph, mailbox_spec="me", auth_mode="delegated", folder_id="f1", path="/Inbox")
    assert isinstance(f, Folder)
    assert f.id == "f1"
    assert f.path == "/Inbox"
    assert graph.get.call_args.args[0] == "/me/mailFolders/f1"


def test_resolve_folder_paths_batches_per_tier():
    """Each tier of folder traversal should issue a single /$batch POST."""
    import json as _json

    import httpx

    from m365ctl.common.graph import GraphClient
    from m365ctl.mail.folders import resolve_folder_paths

    posts: list[dict] = []

    # A simple mailbox tree:
    #  /Inbox            (id=inbox, well-known)
    #  /Inbox/Triage     (id=triage)
    #  /Inbox/Triage/AI  (id=ai)
    #  /Archive          (id=archive)
    root_children = [
        _folder_raw("inbox", "Inbox", child_count=1),
        _folder_raw("archive", "Archive"),
    ]
    inbox_children = [_folder_raw("triage", "Triage", child_count=1)]
    triage_children = [_folder_raw("ai", "AI")]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/$batch"):
            body = _json.loads(request.read())
            posts.append({"path": path, "body": body})
            responses = []
            for r in body["requests"]:
                url = r["url"]
                if url.endswith("/mailFolders/inbox"):
                    sub = {"id": "inbox", "displayName": "Inbox"}
                elif url.endswith("/mailFolders/archive"):
                    sub = {"id": "archive", "displayName": "Archive"}
                elif "/mailFolders/inbox/childFolders" in url:
                    sub = {"value": inbox_children}
                elif "/mailFolders/triage/childFolders" in url:
                    sub = {"value": triage_children}
                else:
                    sub = {"value": []}
                responses.append({
                    "id": r["id"], "status": 200, "headers": {}, "body": sub,
                })
            return httpx.Response(200, json={"responses": responses})
        # Non-batch tier-0 root listing.
        if path.endswith("/me/mailFolders"):
            return httpx.Response(200, json={"value": root_children})
        return httpx.Response(404, json={"error": {"code": "NotFound"}})

    graph = GraphClient(
        token_provider=lambda: "tok",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )

    out = resolve_folder_paths(
        ["inbox", "/Archive", "Inbox/Triage", "Inbox/Triage/AI"],
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
    )
    assert out["inbox"] == "inbox"
    assert out["/Archive"] == "archive"
    assert out["Inbox/Triage"] == "triage"
    assert out["Inbox/Triage/AI"] == "ai"

    # Three batched POSTs:
    #  1. well-known fast-path GETs ("inbox") — only 1 well-known here.
    #  2. tier-1 expansion: childFolders of /Inbox (for both /Inbox/Triage
    #     and /Inbox/Triage/AI; current_id == "inbox" for both).
    #  3. tier-2 expansion: childFolders of /Inbox/Triage (only the AI
    #     path remains).
    assert len(posts) == 3
    # Tier 1 (well-known): one GET for "inbox".
    t1 = posts[0]["body"]["requests"]
    assert all(r["method"] == "GET" for r in t1)
    assert any(r["url"].endswith("/mailFolders/inbox") for r in t1)
    # Tier 2: childFolders of inbox — issued for both "Inbox/Triage" and
    # "Inbox/Triage/AI", so 2 sub-requests.
    t2 = posts[1]["body"]["requests"]
    assert all("/childFolders" in r["url"] for r in t2)
    assert len(t2) == 2
    # Tier 3: childFolders of triage — only the AI path is left.
    t3 = posts[2]["body"]["requests"]
    assert all("/childFolders" in r["url"] for r in t3)
    assert len(t3) == 1


def test_resolve_folder_paths_empty_input():
    from unittest.mock import MagicMock as _MM

    from m365ctl.mail.folders import resolve_folder_paths

    out = resolve_folder_paths([], _MM(), mailbox_spec="me", auth_mode="delegated")
    assert out == {}
