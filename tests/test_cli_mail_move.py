
import json

import httpx

from m365ctl.common.planfile import (
    Operation,
    PLAN_SCHEMA_VERSION,
    Plan,
    write_plan,
)
from m365ctl.mail.cli.move import build_parser


def test_move_parser_single_mode():
    args = build_parser().parse_args([
        "--message-id", "m1",
        "--to-folder", "/Archive",
        "--confirm",
    ])
    assert args.message_id == "m1"
    assert args.to_folder == "/Archive"
    assert args.confirm is True


def test_move_parser_bulk_plan_out():
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


def test_move_parser_from_plan_requires_confirm():
    args = build_parser().parse_args([
        "--from-plan", "/tmp/p.json",
        "--confirm",
    ])
    assert args.from_plan == "/tmp/p.json"
    assert args.confirm is True


def test_move_parser_no_args_still_valid():
    args = build_parser().parse_args([])
    assert args.message_id is None
    assert args.from_plan is None


def test_mail_move_from_plan_uses_batch(tmp_path, monkeypatch):
    """Bulk move via plan file should issue $batch envelopes, not N individual POSTs."""
    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raw = request.read()
        body = json.loads(raw) if raw else None
        posts.append({
            "path": request.url.path,
            "method": request.method,
            "body": body,
        })
        # /$batch envelope expected
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
                                "id": r["url"].split("/")[-1],
                                "parentFolderId": "Inbox",
                            }
                            if r["method"] == "GET"
                            else {
                                "id": r["url"].split("/")[-2],
                                "parentFolderId": "archive",
                            }
                        ),
                    }
                    for r in payload["requests"]
                ],
            })
        # Anything else (e.g., a token check) -> 200 empty.
        return httpx.Response(200, json={})

    # Monkeypatch GraphClient construction in the CLI to inject our transport.
    from m365ctl.common import graph as _graph_mod

    real_graphclient = _graph_mod.GraphClient

    def factory(*, token_provider=None, **_kw):
        return real_graphclient(
            token_provider=token_provider or (lambda: "tok"),
            transport=httpx.MockTransport(handler),
            sleep=lambda _s: None,
        )

    monkeypatch.setattr("m365ctl.mail.cli.move.GraphClient", factory)

    # Stub out load_and_authorize to skip cert/credential plumbing.
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
        "m365ctl.mail.cli.move.load_and_authorize",
        lambda args: (cfg, "delegated", _DummyCred()),
    )

    # Build a plan with 3 mail.move ops.
    plan = Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at="2026-05-01T00:00:00+00:00",
        source_cmd="mail-move",
        scope="me",
        operations=[
            Operation(
                op_id=f"op{i}",
                action="mail.move",
                drive_id="me",
                item_id=f"m{i}",
                args={"destination_id": "archive", "destination_path": "/Archive"},
            )
            for i in range(3)
        ],
    )
    plan_path = tmp_path / "plan.json"
    write_plan(plan, plan_path)

    # Run the CLI in --from-plan mode.
    from m365ctl.mail.cli.move import main as move_main

    rc = move_main([
        "--mailbox", "me",
        "--from-plan", str(plan_path),
        "--confirm",
    ])
    assert rc == 0
    # Two /$batch POSTs: phase 1 (3 GETs), phase 2 (3 move POSTs).
    batch_posts = [p for p in posts if p["path"].endswith("/$batch")]
    assert len(batch_posts) == 2
    phase1, phase2 = batch_posts
    assert all(r["method"] == "GET" for r in phase1["body"]["requests"])
    assert all(r["method"] == "POST" for r in phase2["body"]["requests"])
    assert all(r["url"].endswith("/move") for r in phase2["body"]["requests"])
