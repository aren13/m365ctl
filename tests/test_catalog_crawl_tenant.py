from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from fazla_od.catalog.crawl import DriveSpec, resolve_scope


def test_resolve_scope_site_by_numeric_id_lists_drives() -> None:
    graph = MagicMock()

    def fake_get(path, *, params=None):
        if path == "/sites/site-123":
            return {
                "id": "site-123",
                "displayName": "Finance",
                "webUrl": "https://fazla.sharepoint.com/sites/finance",
            }
        if path == "/sites/site-123/drives":
            return {
                "value": [
                    {
                        "id": "drive-fin-docs",
                        "name": "Documents",
                        "driveType": "documentLibrary",
                        "owner": {"group": {"displayName": "Finance Site"}},
                    }
                ]
            }
        raise AssertionError(f"unexpected path: {path}")

    graph.get.side_effect = fake_get
    drives = resolve_scope("site:site-123", graph)
    assert len(drives) == 1
    assert drives[0] == DriveSpec(
        drive_id="drive-fin-docs",
        display_name="Finance / Documents",
        owner="Finance Site",
        drive_type="documentLibrary",
        graph_path="/drives/drive-fin-docs/root/delta",
    )


def test_resolve_scope_site_by_slug_uses_search() -> None:
    graph = MagicMock()

    def fake_get(path, *, params=None):
        if path == "/sites" and params == {"search": "Finance"}:
            return {
                "value": [
                    {"id": "site-abc", "displayName": "Finance",
                     "webUrl": "https://fazla.sharepoint.com/sites/finance"}
                ]
            }
        if path == "/sites/site-abc":
            return {"id": "site-abc", "displayName": "Finance"}
        if path == "/sites/site-abc/drives":
            return {"value": [
                {"id": "dr1", "name": "Documents",
                 "driveType": "documentLibrary",
                 "owner": {"group": {"displayName": "Finance Site"}}}
            ]}
        raise AssertionError(f"unexpected path: {path} {params}")

    graph.get.side_effect = fake_get
    drives = resolve_scope("site:Finance", graph)
    assert drives[0].drive_id == "dr1"


def test_resolve_scope_site_slug_unique_match_required() -> None:
    graph = MagicMock()
    graph.get.return_value = {
        "value": [
            {"id": "s1", "displayName": "Finance"},
            {"id": "s2", "displayName": "Finance Ops"},
        ]
    }
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_scope("site:Finance", graph)


def test_resolve_scope_site_slug_no_match() -> None:
    graph = MagicMock()
    graph.get.return_value = {"value": []}
    with pytest.raises(ValueError, match="no site"):
        resolve_scope("site:NoSuch", graph)


def test_resolve_scope_tenant_enumerates_users_and_sites() -> None:
    graph = MagicMock()

    def fake_get(path, *, params=None):
        if path == "/users":
            return {
                "value": [
                    {"id": "u1", "userPrincipalName": "a@fazla.com", "displayName": "A"},
                    {"id": "u2", "userPrincipalName": "b@fazla.com", "displayName": "B"},
                ]
            }
        if path == "/users/u1/drive":
            return {"id": "drv-u1", "name": "OneDrive - Fazla",
                    "driveType": "business",
                    "owner": {"user": {"email": "a@fazla.com"}}}
        if path == "/users/u2/drive":
            # Simulate a user without a provisioned drive (HTTP 404 → raises)
            from fazla_od.graph import GraphError
            raise GraphError("itemNotFound: no drive")
        if path == "/sites" and params == {"search": "*"}:
            return {"value": [
                {"id": "site-1", "displayName": "Finance"},
            ]}
        if path == "/sites/site-1":
            return {"id": "site-1", "displayName": "Finance"}
        if path == "/sites/site-1/drives":
            return {"value": [
                {"id": "drv-fin", "name": "Documents",
                 "driveType": "documentLibrary",
                 "owner": {"group": {"displayName": "Finance"}}}
            ]}
        raise AssertionError(f"unexpected path: {path} {params}")

    graph.get.side_effect = fake_get
    drives = resolve_scope("tenant", graph)

    ids = sorted(d.drive_id for d in drives)
    assert ids == ["drv-fin", "drv-u1"]  # u2's missing drive silently skipped
    # DriveSpec.graph_path is the delta path
    fin = next(d for d in drives if d.drive_id == "drv-fin")
    assert fin.graph_path == "/drives/drv-fin/root/delta"


def test_resolve_scope_tenant_skips_resourcenotfound_mysite() -> None:
    """Regression: Graph returns 'ResourceNotFound: User's mysite not found.'
    (not 'itemNotFound') for unlicensed/guest/never-signed-in accounts.
    Discovered during Plan 3 Task 12 live smoke test."""
    graph = MagicMock()

    def fake_get(path, *, params=None):
        if path == "/users":
            return {"value": [
                {"id": "u1", "userPrincipalName": "a@fazla.com"},
                {"id": "u-guest", "userPrincipalName": "g@fazla.com"},
            ]}
        if path == "/users/u1/drive":
            return {"id": "drv-u1", "name": "OneDrive - Fazla",
                    "driveType": "business",
                    "owner": {"user": {"email": "a@fazla.com"}}}
        if path == "/users/u-guest/drive":
            from fazla_od.graph import GraphError
            raise GraphError("ResourceNotFound: User's mysite not found.")
        if path == "/sites" and params == {"search": "*"}:
            return {"value": []}
        raise AssertionError(f"unexpected path: {path}")

    graph.get.side_effect = fake_get
    drives = resolve_scope("tenant", graph)
    assert [d.drive_id for d in drives] == ["drv-u1"]


def test_resolve_scope_tenant_paginates_users() -> None:
    graph = MagicMock()

    def fake_get(path, *, params=None):
        if path == "/users":
            return {"value": [
                {"id": "u1", "userPrincipalName": "a@fazla.com"}
            ]}
        if path == "/users/u1/drive":
            return {"id": "drv-u1", "name": "OneDrive",
                    "driveType": "business",
                    "owner": {"user": {"email": "a@fazla.com"}}}
        if path == "/sites" and params == {"search": "*"}:
            return {"value": []}
        raise AssertionError(f"unexpected: {path}")

    graph.get.side_effect = fake_get

    # get_paginated: two pages of users.
    def fake_paginated(path, *, params=None):
        if path == "/users":
            yield [{"id": "u1", "userPrincipalName": "a@fazla.com"}], None
        elif path == "/sites":
            yield [], None
        else:
            raise AssertionError(path)

    graph.get_paginated.side_effect = fake_paginated
    drives = resolve_scope("tenant", graph)
    assert {d.drive_id for d in drives} == {"drv-u1"}


def test_resolve_scope_still_supports_me_and_drive() -> None:
    graph = MagicMock()
    graph.get.return_value = {
        "id": "drv-me",
        "driveType": "business",
        "owner": {"user": {"email": "x@fazla.com"}},
        "name": "OneDrive",
    }
    drives = resolve_scope("me", graph)
    assert drives[0].drive_id == "drv-me"

    graph.get.return_value = {
        "id": "drv-xyz",
        "driveType": "documentLibrary",
        "owner": {"user": {"email": "s@fazla.com"}},
        "name": "Finance",
    }
    drives = resolve_scope("drive:drv-xyz", graph)
    assert drives[0].drive_id == "drv-xyz"
