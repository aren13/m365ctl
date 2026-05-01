from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from m365ctl.onedrive.catalog.crawl import DriveSpec, resolve_scope


def test_resolve_scope_site_by_numeric_id_lists_drives() -> None:
    graph = MagicMock()

    def fake_get(path, *, params=None):
        if path == "/sites/site-123":
            return {
                "id": "site-123",
                "displayName": "Finance",
                "webUrl": "https://contoso.sharepoint.com/sites/finance",
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
                     "webUrl": "https://contoso.sharepoint.com/sites/finance"}
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
                    {"id": "u1", "userPrincipalName": "a@example.com", "displayName": "A"},
                    {"id": "u2", "userPrincipalName": "b@example.com", "displayName": "B"},
                ]
            }
        if path == "/users/u1/drive":
            return {"id": "drv-u1", "name": "OneDrive - Example",
                    "driveType": "business",
                    "owner": {"user": {"email": "a@example.com"}}}
        if path == "/users/u2/drive":
            # Simulate a user without a provisioned drive (HTTP 404 → raises)
            from m365ctl.common.graph import GraphError
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


def test_resolve_scope_tenant_skips_notallowed_access_blocked() -> None:
    """Regression: Graph returns 'notAllowed: Access to this site has been
    blocked.' for tenant-admin-blocked users (retention/legal hold). Must
    skip, not abort. Discovered during a tenant-scope live smoke test."""
    graph = MagicMock()

    def fake_get(path, *, params=None):
        if path == "/users":
            return {"value": [
                {"id": "u1", "userPrincipalName": "a@example.com"},
                {"id": "u-blocked", "userPrincipalName": "blocked@example.com"},
            ]}
        if path == "/users/u1/drive":
            return {"id": "drv-u1", "name": "OneDrive - Example",
                    "driveType": "business",
                    "owner": {"user": {"email": "a@example.com"}}}
        if path == "/users/u-blocked/drive":
            from m365ctl.common.graph import GraphError
            raise GraphError(
                "notAllowed: Access to this site has been blocked."
            )
        if path == "/sites" and params == {"search": "*"}:
            return {"value": []}
        raise AssertionError(f"unexpected path: {path}")

    graph.get.side_effect = fake_get
    drives = resolve_scope("tenant", graph)
    assert [d.drive_id for d in drives] == ["drv-u1"]


def test_resolve_scope_tenant_skips_resourcenotfound_mysite() -> None:
    """Regression: Graph returns 'ResourceNotFound: User's mysite not found.'
    (not 'itemNotFound') for unlicensed/guest/never-signed-in accounts.
    Discovered during a tenant-scope live smoke test."""
    graph = MagicMock()

    def fake_get(path, *, params=None):
        if path == "/users":
            return {"value": [
                {"id": "u1", "userPrincipalName": "a@example.com"},
                {"id": "u-guest", "userPrincipalName": "g@example.com"},
            ]}
        if path == "/users/u1/drive":
            return {"id": "drv-u1", "name": "OneDrive - Example",
                    "driveType": "business",
                    "owner": {"user": {"email": "a@example.com"}}}
        if path == "/users/u-guest/drive":
            from m365ctl.common.graph import GraphError
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
                {"id": "u1", "userPrincipalName": "a@example.com"}
            ]}
        if path == "/users/u1/drive":
            return {"id": "drv-u1", "name": "OneDrive",
                    "driveType": "business",
                    "owner": {"user": {"email": "a@example.com"}}}
        if path == "/sites" and params == {"search": "*"}:
            return {"value": []}
        raise AssertionError(f"unexpected: {path}")

    graph.get.side_effect = fake_get

    # get_paginated: two pages of users.
    def fake_paginated(path, *, params=None):
        if path == "/users":
            yield [{"id": "u1", "userPrincipalName": "a@example.com"}], None
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
        "owner": {"user": {"email": "x@example.com"}},
        "name": "OneDrive",
    }
    drives = resolve_scope("me", graph)
    assert drives[0].drive_id == "drv-me"

    graph.get.return_value = {
        "id": "drv-xyz",
        "driveType": "documentLibrary",
        "owner": {"user": {"email": "s@example.com"}},
        "name": "Finance",
    }
    drives = resolve_scope("drive:drv-xyz", graph)
    assert drives[0].drive_id == "drv-xyz"


def test_resolve_scope_tenant_batches_per_user_and_per_site_metadata() -> None:
    """Real GraphClient: per-user /drive and per-site /sites/{id} (+ /drives)
    metadata GETs fan out via /$batch instead of N sequential GETs.
    """
    import json as _json

    import httpx

    from m365ctl.common.graph import GraphClient

    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/$batch"):
            body = _json.loads(request.read())
            posts.append({"path": path, "body": body})
            responses = []
            for r in body["requests"]:
                url = r["url"]
                if url.startswith("users/") and url.endswith("/drive"):
                    uid = url.split("/")[1]
                    responses.append({
                        "id": r["id"], "status": 200, "headers": {},
                        "body": {
                            "id": f"drv-{uid}", "name": f"OneDrive-{uid}",
                            "driveType": "business",
                            "owner": {"user": {"email": f"{uid}@example.com"}},
                        },
                    })
                elif url.startswith("sites/") and url.endswith("/drives"):
                    sid = url.split("/")[1]
                    responses.append({
                        "id": r["id"], "status": 200, "headers": {},
                        "body": {"value": [{
                            "id": f"drv-{sid}-doc", "name": "Documents",
                            "driveType": "documentLibrary",
                            "owner": {"group": {"displayName": sid}},
                        }]},
                    })
                elif url.startswith("sites/"):
                    sid = url.split("/")[1]
                    responses.append({
                        "id": r["id"], "status": 200, "headers": {},
                        "body": {"id": sid, "displayName": sid.title()},
                    })
                else:
                    responses.append({
                        "id": r["id"], "status": 404, "headers": {},
                        "body": {"error": {"code": "NotFound"}},
                    })
            return httpx.Response(200, json={"responses": responses})
        if path.endswith("/users"):
            return httpx.Response(200, json={"value": [
                {"id": "u1", "userPrincipalName": "u1@example.com"},
                {"id": "u2", "userPrincipalName": "u2@example.com"},
            ]})
        if path.endswith("/sites"):
            return httpx.Response(200, json={"value": [
                {"id": "site-a"}, {"id": "site-b"},
            ]})
        return httpx.Response(404, json={"error": {"code": "NotFound"}})

    graph = GraphClient(
        token_provider=lambda: "tok",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )
    drives = resolve_scope("tenant", graph)
    drv_ids = sorted(d.drive_id for d in drives)
    assert drv_ids == ["drv-site-a-doc", "drv-site-b-doc", "drv-u1", "drv-u2"]

    # Three /$batch POSTs: user-drives, site-metadata, site-drives.
    assert len(posts) == 3
    user_batch = posts[0]["body"]["requests"]
    assert all(r["url"].endswith("/drive") and r["url"].startswith("users/")
               for r in user_batch)
    site_batch = posts[1]["body"]["requests"]
    assert all(r["url"].startswith("sites/") and not r["url"].endswith("/drives")
               for r in site_batch)
    site_drives_batch = posts[2]["body"]["requests"]
    assert all(r["url"].endswith("/drives") for r in site_drives_batch)
