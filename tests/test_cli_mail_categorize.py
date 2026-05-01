from m365ctl.mail.cli.categorize import _resolve_final_categories, build_parser


def test_categorize_parser_add():
    args = build_parser().parse_args(["--message-id", "m1", "--add", "X", "--confirm"])
    assert args.add == ["X"]


def test_categorize_parser_set_repeated():
    args = build_parser().parse_args(["--message-id", "m1", "--set", "X", "--set", "Y"])
    assert args.set_ == ["X", "Y"]


def test_categorize_parser_from_plan():
    args = build_parser().parse_args(["--from-plan", "/tmp/p.json", "--confirm"])
    assert args.from_plan == "/tmp/p.json"


def test_resolve_final_set_replaces():
    out = _resolve_final_categories(["A", "B"], [], [], ["X", "Y"])
    assert out == ["X", "Y"]


def test_resolve_final_add_removes_dedup():
    out = _resolve_final_categories(["A"], ["B", "A"], [], [])
    assert out == ["A", "B"]


def test_resolve_final_remove():
    out = _resolve_final_categories(["A", "B", "C"], [], ["B"], [])
    assert out == ["A", "C"]


def test_mail_categorize_from_plan_uses_batch(tmp_path, monkeypatch):
    """Bulk categorize via plan file should issue $batch envelopes."""
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
                            {"id": r["url"].split("/")[-1].split("?")[0],
                             "categories": ["Old"]}
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

    monkeypatch.setattr("m365ctl.mail.cli.categorize.GraphClient", factory)

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
        "m365ctl.mail.cli.categorize.load_and_authorize",
        lambda args: (cfg, "delegated", _DummyCred()),
    )

    plan = Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at="2026-05-01T00:00:00+00:00",
        source_cmd="mail-categorize",
        scope="me",
        operations=[
            Operation(
                op_id=f"op{i}",
                action="mail.categorize",
                drive_id="me",
                item_id=f"m{i}",
                args={"categories": ["Important"]},
            )
            for i in range(3)
        ],
    )
    plan_path = tmp_path / "plan.json"
    write_plan(plan, plan_path)

    from m365ctl.mail.cli.categorize import main as cat_main

    rc = cat_main([
        "--mailbox", "me",
        "--from-plan", str(plan_path),
        "--confirm",
    ])
    assert rc == 0
    batch_posts = [p for p in posts if p["path"].endswith("/$batch")]
    assert len(batch_posts) == 2
    phase1, phase2 = batch_posts
    assert all(r["method"] == "GET" for r in phase1["body"]["requests"])
    assert all(r["method"] == "PATCH" for r in phase2["body"]["requests"])


def test_categorize_parser_bulk_filter_flags():
    """Bulk-mode filter flags parse correctly with the same shape as `mail move`."""
    args = build_parser().parse_args([
        "--from", "alice@example.com",
        "--subject", "vip",
        "--folder", "/Inbox",
        "--category", "VIP",
        "--remove", "VIP",
        "--plan-out", "/tmp/p.json",
    ])
    assert args.from_address == "alice@example.com"
    assert args.subject_contains == "vip"
    assert args.folder == "/Inbox"
    assert args.category == "VIP"
    assert args.remove == ["VIP"]
    assert args.plan_out == "/tmp/p.json"
    assert args.message_id is None
    assert args.confirm is False
    assert args.limit == 50
    assert args.page_size == 50


def test_categorize_parser_bulk_filter_full_set():
    """All filter flags from `mail move` parse on `mail categorize`."""
    args = build_parser().parse_args([
        "--folder", "Inbox",
        "--unread",
        "--has-attachments",
        "--importance", "high",
        "--focus", "focused",
        "--since", "2026-01-01T00:00:00Z",
        "--until", "2026-04-30T00:00:00Z",
        "--limit", "10",
        "--page-size", "25",
        "--add", "Important",
    ])
    assert args.unread is True
    assert args.has_attachments is True
    assert args.importance == "high"
    assert args.focus == "focused"
    assert args.since == "2026-01-01T00:00:00Z"
    assert args.until == "2026-04-30T00:00:00Z"
    assert args.limit == 10
    assert args.page_size == 25
    assert args.add == ["Important"]


def test_mail_categorize_bulk_plan_out_writes_remove_delta(tmp_path, monkeypatch):
    """Bulk dry-run with --remove writes a plan whose per-op `categories`
    arg equals the message's current categories minus the --remove set.
    """
    import json

    from m365ctl.common.planfile import load_plan
    from m365ctl.mail.cli import categorize as cli_cat
    from m365ctl.mail.models import Message

    # Stub auth + scope plumbing.
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
        "m365ctl.mail.cli.categorize.load_and_authorize",
        lambda args: (cfg, "delegated", _DummyCred()),
    )
    monkeypatch.setattr(
        "m365ctl.mail.cli.categorize.GraphClient",
        lambda **_kw: object(),
    )
    monkeypatch.setattr(
        "m365ctl.mail.cli.categorize.resolve_folder_path",
        lambda *a, **kw: "fid-inbox",
    )

    # Each message has different existing categories so we can verify
    # per-message subtraction.
    def _msg(msg_id: str, cats: list[str]) -> Message:
        return Message.from_graph_json(
            {
                "id": msg_id,
                "subject": f"subj-{msg_id}",
                "receivedDateTime": "2026-04-01T00:00:00Z",
                "categories": cats,
                "from": {"emailAddress": {"address": "a@x.com", "name": "A"}},
            },
            mailbox_upn="me",
            parent_folder_path="Inbox",
        )

    fixtures = [
        _msg("m1", ["VIP", "Followup"]),
        _msg("m2", ["VIP"]),
        _msg("m3", ["Other"]),
    ]

    def fake_expand(*args, **kwargs):
        for m in fixtures:
            yield m

    monkeypatch.setattr(
        "m365ctl.mail.cli.categorize.expand_messages_for_pattern",
        fake_expand,
    )

    plan_path = tmp_path / "plan.json"
    rc = cli_cat.main([
        "--mailbox", "me",
        "--folder", "Inbox",
        "--remove", "VIP",
        "--plan-out", str(plan_path),
    ])
    assert rc == 0
    assert plan_path.exists()
    plan = load_plan(plan_path)
    assert len(plan.operations) == 3
    by_id = {op.item_id: op for op in plan.operations}
    # m1 had ["VIP", "Followup"] → ["Followup"]
    assert by_id["m1"].action == "mail.categorize"
    assert by_id["m1"].args["categories"] == ["Followup"]
    # m2 had ["VIP"] → []
    assert by_id["m2"].args["categories"] == []
    # m3 had ["Other"] (no VIP) → ["Other"]
    assert by_id["m3"].args["categories"] == ["Other"]
    # Plan file is well-formed JSON with mail.categorize ops.
    raw = json.loads(plan_path.read_text())
    assert raw["operations"][0]["action"] == "mail.categorize"
