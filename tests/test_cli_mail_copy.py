
from m365ctl.mail.cli.copy import build_parser


def test_copy_parser_single_mode():
    args = build_parser().parse_args([
        "--message-id", "m1",
        "--to-folder", "/Archive",
        "--confirm",
    ])
    assert args.message_id == "m1"
    assert args.to_folder == "/Archive"
    assert args.confirm is True


def test_copy_parser_bulk_plan_out():
    args = build_parser().parse_args([
        "--from", "alice@example.com",
        "--subject", "old",
        "--folder", "/Inbox",
        "--to-folder", "/Archive/Old",
        "--plan-out", "/tmp/p.json",
    ])
    assert args.from_address == "alice@example.com"
    assert args.subject_contains == "old"
    assert args.folder == "/Inbox"
    assert args.to_folder == "/Archive/Old"
    assert args.plan_out == "/tmp/p.json"
    assert args.confirm is False


def test_copy_parser_from_plan_requires_confirm():
    args = build_parser().parse_args([
        "--from-plan", "/tmp/p.json",
        "--confirm",
    ])
    assert args.from_plan == "/tmp/p.json"
    assert args.confirm is True


def test_copy_parser_no_args_still_valid():
    args = build_parser().parse_args([])
    assert args.message_id is None
    assert args.from_plan is None


def test_mail_copy_from_plan_uses_batch(tmp_path, monkeypatch):
    """Bulk copy via plan file should issue $batch envelopes."""
    import json
    import httpx
    from m365ctl.common.planfile import (
        Operation,
        PLAN_SCHEMA_VERSION,
        Plan,
        write_plan,
    )

    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raw = request.read()
        body = json.loads(raw) if raw else None
        posts.append({
            "path": request.url.path,
            "method": request.method,
            "body": body,
        })
        if request.url.path.endswith("/$batch"):
            payload = body
            return httpx.Response(200, json={
                "responses": [
                    {
                        "id": r["id"],
                        "status": 200,
                        "headers": {},
                        "body": (
                            {"id": r["url"].split("/")[-1].split("?")[0]}
                            if r["method"] == "GET"
                            else {"id": "newmsg-" + r["url"].split("/")[-2]}
                        ),
                    }
                    for r in payload["requests"]
                ],
            })
        return httpx.Response(200, json={})

    from m365ctl.common import graph as _graph_mod

    real_graphclient = _graph_mod.GraphClient

    def factory(*, token_provider=None, **_kw):
        return real_graphclient(
            token_provider=token_provider or (lambda: "tok"),
            transport=httpx.MockTransport(handler),
            sleep=lambda _s: None,
        )

    monkeypatch.setattr("m365ctl.mail.cli.copy.GraphClient", factory)

    from m365ctl.common.config import (
        CatalogConfig,
        Config,
        LoggingConfig,
        MailConfig,
        ScopeConfig,
    )

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
        "m365ctl.mail.cli.copy.load_and_authorize",
        lambda args: (cfg, "delegated", _DummyCred()),
    )

    plan = Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at="2026-05-01T00:00:00+00:00",
        source_cmd="mail-copy",
        scope="me",
        operations=[
            Operation(
                op_id=f"op{i}",
                action="mail.copy",
                drive_id="me",
                item_id=f"m{i}",
                args={"destination_id": "archive", "destination_path": "/Archive"},
            )
            for i in range(3)
        ],
    )
    plan_path = tmp_path / "plan.json"
    write_plan(plan, plan_path)

    from m365ctl.mail.cli.copy import main as copy_main

    rc = copy_main([
        "--mailbox", "me",
        "--from-plan", str(plan_path),
        "--confirm",
    ])
    assert rc == 0
    batch_posts = [p for p in posts if p["path"].endswith("/$batch")]
    assert len(batch_posts) == 2
    phase1, phase2 = batch_posts
    assert all(r["method"] == "GET" for r in phase1["body"]["requests"])
    assert all(r["method"] == "POST" for r in phase2["body"]["requests"])
    assert all(r["url"].endswith("/copy") for r in phase2["body"]["requests"])
