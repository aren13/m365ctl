
import json

import httpx

from m365ctl.common.planfile import (
    Operation,
    PLAN_SCHEMA_VERSION,
    Plan,
    write_plan,
)
from m365ctl.mail.cli.delete import build_parser


def test_delete_parser_single_mode():
    args = build_parser().parse_args([
        "--message-id", "m1",
        "--confirm",
    ])
    assert args.message_id == "m1"
    assert args.confirm is True


def test_delete_parser_bulk_plan_out():
    args = build_parser().parse_args([
        "--from", "alice@example.com",
        "--subject", "spam",
        "--folder", "/Inbox",
        "--plan-out", "/tmp/p.json",
    ])
    assert args.from_address == "alice@example.com"
    assert args.subject_contains == "spam"
    assert args.folder == "/Inbox"
    assert args.plan_out == "/tmp/p.json"
    assert args.confirm is False


def test_delete_parser_from_plan():
    args = build_parser().parse_args([
        "--from-plan", "/tmp/p.json",
        "--confirm",
    ])
    assert args.from_plan == "/tmp/p.json"
    assert args.confirm is True


def test_delete_parser_no_args_still_valid():
    args = build_parser().parse_args([])
    assert args.message_id is None
    assert args.from_plan is None
    assert args.plan_out is None


def test_delete_help_mentions_hard_delete_distinction():
    """Per spec: --help explicitly distinguishes from mail-clean (hard delete, Phase 6)."""
    parser = build_parser()
    help_text = parser.format_help()
    assert "clean" in help_text.lower() or "hard" in help_text.lower()


def test_delete_parser_accepts_assume_yes():
    args = build_parser().parse_args(["--assume-yes"])
    assert args.assume_yes is True


def test_assume_yes_rejected_when_config_disallows(tmp_path):
    """``--assume-yes`` errors out cleanly when config opts out (default)."""
    import argparse
    from m365ctl.common.config import (
        CatalogConfig, Config, LoggingConfig, MailConfig, SafetyConfig, ScopeConfig,
    )
    from m365ctl.mail.cli._common import _validate_assume_yes
    cfg = Config(
        tenant_id="t", client_id="c",
        cert_path=tmp_path / "k", cert_public=tmp_path / "c",
        default_auth="delegated",
        scope=ScopeConfig(allow_drives=["me"]),
        catalog=CatalogConfig(path=tmp_path / "x.duckdb"),
        logging=LoggingConfig(ops_dir=tmp_path / "logs"),
        mail=MailConfig(catalog_path=tmp_path / "m.duckdb"),
        safety=SafetyConfig(allow_no_tty_confirm=False),
    )
    args = argparse.Namespace(assume_yes=True, config="config.toml")
    try:
        _validate_assume_yes(cfg, args)
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("expected SystemExit(2)")


def test_assume_yes_passes_when_config_allows(tmp_path):
    from m365ctl.mail.cli._common import _validate_assume_yes
    from m365ctl.common.config import (
        Config, ScopeConfig, SafetyConfig, CatalogConfig, LoggingConfig, MailConfig,
    )
    import argparse
    cfg = Config(
        tenant_id="t", client_id="c",
        cert_path=tmp_path / "k", cert_public=tmp_path / "c",
        default_auth="delegated",
        scope=ScopeConfig(allow_drives=["me"]),
        catalog=CatalogConfig(path=tmp_path / "x.duckdb"),
        logging=LoggingConfig(ops_dir=tmp_path / "logs"),
        mail=MailConfig(catalog_path=tmp_path / "m.duckdb"),
        safety=SafetyConfig(allow_no_tty_confirm=True),
    )
    args = argparse.Namespace(assume_yes=True, config="config.toml")
    _validate_assume_yes(cfg, args)  # no raise


def test_mail_delete_from_plan_uses_batch(tmp_path, monkeypatch):
    """Bulk soft-delete via plan file should issue $batch envelopes."""
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
                                "parentFolderId": "Inbox",
                            }
                            if r["method"] == "GET"
                            else {
                                "id": r["url"].split("/")[-2],
                                "parentFolderId": "deleteditems-id",
                            }
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

    monkeypatch.setattr("m365ctl.mail.cli.delete.GraphClient", factory)

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
        "m365ctl.mail.cli.delete.load_and_authorize",
        lambda args: (cfg, "delegated", _DummyCred()),
    )

    plan = Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at="2026-05-01T00:00:00+00:00",
        source_cmd="mail-delete",
        scope="me",
        operations=[
            Operation(
                op_id=f"op{i}",
                action="mail.delete.soft",
                drive_id="me",
                item_id=f"m{i}",
                args={},
            )
            for i in range(3)
        ],
    )
    plan_path = tmp_path / "plan.json"
    write_plan(plan, plan_path)

    from m365ctl.mail.cli.delete import main as delete_main

    rc = delete_main([
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
    assert all(r["url"].endswith("/move") for r in phase2["body"]["requests"])
