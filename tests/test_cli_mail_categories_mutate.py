
import json

import httpx

from m365ctl.mail.cli.categories import build_parser


def test_categories_list_still_works_with_no_subcommand():
    args = build_parser().parse_args([])
    assert args.subcommand is None


def test_categories_list_subcommand_still_works():
    args = build_parser().parse_args(["list"])
    assert args.subcommand == "list"


def test_categories_add_subparser():
    args = build_parser().parse_args(["add", "Followup", "--color", "preset0", "--confirm"])
    assert args.subcommand == "add"
    assert args.name == "Followup"
    assert args.color == "preset0"
    assert args.confirm is True


def test_categories_add_default_color():
    args = build_parser().parse_args(["add", "X"])
    assert args.color == "preset0"


def test_categories_update_subparser():
    args = build_parser().parse_args(["update", "cat-id", "--name", "New", "--color", "preset2", "--confirm"])
    assert args.subcommand == "update"
    assert args.id == "cat-id"
    assert args.name == "New"
    assert args.color == "preset2"


def test_categories_remove_subparser():
    args = build_parser().parse_args(["remove", "cat-id", "--confirm"])
    assert args.subcommand == "remove"
    assert args.id == "cat-id"
    assert args.strip_from_messages is False


def test_categories_remove_strip_from_messages_flag():
    args = build_parser().parse_args([
        "remove", "cat-id", "--strip-from-messages", "--confirm",
    ])
    assert args.subcommand == "remove"
    assert args.id == "cat-id"
    assert args.strip_from_messages is True
    assert args.confirm is True


def test_categories_sync_subparser():
    args = build_parser().parse_args(["sync", "--confirm"])
    assert args.subcommand == "sync"


def _stub_cli_env(monkeypatch, tmp_path, handler):
    """Wire MockTransport + dummy cred/config into mail.cli.categories."""
    from m365ctl.common import graph as _graph_mod
    from m365ctl.common.config import (
        CatalogConfig,
        Config,
        LoggingConfig,
        MailConfig,
        ScopeConfig,
    )

    real_graphclient = _graph_mod.GraphClient

    def factory(*, token_provider=None, **_kw):
        return real_graphclient(
            token_provider=token_provider or (lambda: "tok"),
            transport=httpx.MockTransport(handler),
            sleep=lambda _s: None,
        )

    monkeypatch.setattr("m365ctl.mail.cli.categories.GraphClient", factory)

    cfg = Config(
        tenant_id="t",
        client_id="a",
        cert_path=tmp_path / "cert.pem",
        cert_public=tmp_path / "cert-pub.pem",
        default_auth="delegated",
        scope=ScopeConfig(allow_drives=["me"]),
        catalog=CatalogConfig(path=tmp_path / "catalog.duckdb"),
        mail=MailConfig(catalog_path=tmp_path / "mail.duckdb"),
        logging=LoggingConfig(ops_dir=tmp_path / "ops"),
    )

    class _DummyCred:
        def get_token(self):
            return "tok"

    monkeypatch.setattr(
        "m365ctl.mail.cli.categories.load_and_authorize",
        lambda args: (cfg, "delegated", _DummyCred()),
    )
    monkeypatch.setattr(
        "m365ctl.mail.cli.categories.assert_mail_target_allowed",
        lambda *a, **k: None,
    )


def test_mail_categories_remove_default_only_deletes_master(tmp_path, monkeypatch):
    """Without --strip-from-messages: only master DELETE is issued (current behavior)."""
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raw = request.read()
        body = json.loads(raw) if raw else None
        requests.append({
            "path": request.url.path,
            "query": request.url.query.decode() if request.url.query else "",
            "method": request.method,
            "body": body,
        })
        if request.url.path.endswith("/outlook/masterCategories"):
            # Used by list_master_categories to populate `before`.
            return httpx.Response(200, json={
                "value": [
                    {"id": "cat-id", "displayName": "Followup", "color": "preset0"},
                ],
            })
        if request.url.path == "/v1.0/me/outlook/masterCategories/cat-id":
            assert request.method == "DELETE"
            return httpx.Response(204)
        return httpx.Response(200, json={})

    _stub_cli_env(monkeypatch, tmp_path, handler)

    from m365ctl.mail.cli.categories import main as categories_main

    rc = categories_main([
        "--mailbox", "me",
        "remove", "cat-id", "--confirm",
    ])
    assert rc == 0
    # No /$batch envelopes — strip flag was not set.
    batch_posts = [r for r in requests if r["path"].endswith("/$batch")]
    assert batch_posts == []
    # No per-message PATCHes.
    patches = [r for r in requests if r["method"] == "PATCH"]
    assert patches == []
    # The master DELETE happened.
    deletes = [r for r in requests if r["method"] == "DELETE"]
    assert len(deletes) == 1
    assert deletes[0]["path"].endswith("/outlook/masterCategories/cat-id")


def test_mail_categories_remove_strip_from_messages_batches_then_deletes(
    tmp_path, monkeypatch,
):
    """With --strip-from-messages: per-message PATCHes via /$batch, THEN master DELETE."""
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raw = request.read()
        body = json.loads(raw) if raw else None
        requests.append({
            "path": request.url.path,
            "query": request.url.query.decode() if request.url.query else "",
            "method": request.method,
            "body": body,
        })
        # 1) GET master categories (for `before` resolution).
        if (
            request.method == "GET"
            and request.url.path == "/v1.0/me/outlook/masterCategories"
        ):
            return httpx.Response(200, json={
                "value": [
                    {"id": "cat-id", "displayName": "Followup", "color": "preset0"},
                ],
            })
        # 2) GET messages filtered by category (strip search pass).
        if (
            request.method == "GET"
            and request.url.path == "/v1.0/me/messages"
        ):
            return httpx.Response(200, json={
                "value": [
                    {"id": "m1", "categories": ["Followup", "Other"]},
                    {"id": "m2", "categories": ["Followup"]},
                ],
            })
        # 3) /$batch envelopes for the per-message PATCH pass.
        if request.url.path.endswith("/$batch"):
            payload = body
            return httpx.Response(200, json={
                "responses": [
                    {
                        "id": r["id"],
                        "status": (
                            200 if r["method"] == "GET" else 204
                        ),
                        "headers": {},
                        "body": (
                            {
                                "id": r["url"].split("/")[-1].split("?")[0],
                                "categories": ["Followup", "Other"],
                            }
                            if r["method"] == "GET"
                            else None
                        ),
                    }
                    for r in payload["requests"]
                ],
            })
        # 4) DELETE the master category record.
        if (
            request.method == "DELETE"
            and request.url.path == "/v1.0/me/outlook/masterCategories/cat-id"
        ):
            return httpx.Response(204)
        return httpx.Response(200, json={})

    _stub_cli_env(monkeypatch, tmp_path, handler)

    from m365ctl.mail.cli.categories import main as categories_main

    rc = categories_main([
        "--mailbox", "me",
        "remove", "cat-id", "--strip-from-messages", "--confirm",
    ])
    assert rc == 0

    # Order matters: the master DELETE must come AFTER at least one /$batch
    # POST (the strip pass).
    batch_posts = [
        i for i, r in enumerate(requests)
        if r["path"].endswith("/$batch")
    ]
    delete_idx = next(
        (
            i for i, r in enumerate(requests)
            if r["method"] == "DELETE"
            and r["path"] == "/v1.0/me/outlook/masterCategories/cat-id"
        ),
        None,
    )
    assert batch_posts, "expected at least one /$batch POST for the strip pass"
    assert delete_idx is not None, "expected master-category DELETE"
    assert delete_idx > batch_posts[-1], (
        "master DELETE must run AFTER the per-message strip /$batch pass"
    )

    # Phase-2 batch must include PATCH sub-requests stripping the tag.
    patch_subs: list[dict] = []
    for i in batch_posts:
        for sub in requests[i]["body"]["requests"]:
            if sub["method"] == "PATCH":
                patch_subs.append(sub)
    assert len(patch_subs) == 2  # one per message
    for sub in patch_subs:
        assert "Followup" not in sub["body"]["categories"]
