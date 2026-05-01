import pytest
from m365ctl.mail.cli.flag import build_parser


def test_flag_parser_single_item():
    args = build_parser().parse_args([
        "--message-id", "m1",
        "--status", "flagged",
        "--due", "2026-04-30T17:00:00Z",
        "--confirm",
    ])
    assert args.message_id == "m1"
    assert args.status == "flagged"
    assert args.due == "2026-04-30T17:00:00Z"
    assert args.confirm is True


def test_flag_parser_rejects_invalid_status():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--status", "maybe"])


def test_flag_parser_from_plan():
    args = build_parser().parse_args(["--from-plan", "/tmp/p.json", "--confirm"])
    assert args.from_plan == "/tmp/p.json"


def test_mail_flag_from_plan_uses_batch(tmp_path, monkeypatch):
    """Bulk flag via plan file issues TWO $batch envelopes (Phase-1 GETs + Phase-2 PATCHes)."""
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
                            {
                                "id": r["url"].split("/")[-1].split("?")[0],
                                "flag": {"flagStatus": "notFlagged"},
                            }
                            if r["method"] == "GET"
                            else {}
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

    monkeypatch.setattr("m365ctl.mail.cli.flag.GraphClient", factory)

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
        "m365ctl.mail.cli.flag.load_and_authorize",
        lambda args: (cfg, "delegated", _DummyCred()),
    )

    plan = Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at="2026-05-01T00:00:00+00:00",
        source_cmd="mail-flag",
        scope="me",
        operations=[
            Operation(
                op_id=f"op{i}",
                action="mail.flag",
                drive_id="me",
                item_id=f"m{i}",
                args={"status": "flagged"},
            )
            for i in range(3)
        ],
    )
    plan_path = tmp_path / "plan.json"
    write_plan(plan, plan_path)

    from m365ctl.mail.cli.flag import main as flag_main

    rc = flag_main([
        "--mailbox", "me",
        "--from-plan", str(plan_path),
        "--confirm",
    ])
    assert rc == 0
    batch_posts = [p for p in posts if p["path"].endswith("/$batch")]
    # Phase 1: GETs to capture pre-flag state for undo. Phase 2: PATCHes.
    assert len(batch_posts) == 2
    phase1, phase2 = batch_posts
    assert all(r["method"] == "GET" for r in phase1["body"]["requests"])
    assert all(r["method"] == "PATCH" for r in phase2["body"]["requests"])
