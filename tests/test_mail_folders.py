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
