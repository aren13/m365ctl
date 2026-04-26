"""Tests for `m365ctl mail digest` CLI."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""
tenant_id = "tenant"
client_id = "client"
cert_path = "{tmp_path / 'c.pem'}"
cert_public = "{tmp_path / 'p.cer'}"
default_auth = "delegated"

[scope]
allow_drives = ["me"]
allow_mailboxes = ["me"]

[catalog]
path = "{tmp_path / 'cat.duckdb'}"

[mail]
catalog_path = "{tmp_path / 'mail.duckdb'}"

[logging]
ops_dir = "{tmp_path / 'logs'}"
"""
    )
    return cfg


def _seed_catalog(catalog_path: Path, *, now: datetime) -> None:
    """Seed catalog with a few unread messages."""
    from m365ctl.mail.catalog.db import open_catalog
    rows = [
        ("me", "m1", "Quarterly review", "alice@example.com",
         now - timedelta(hours=1), False, False, "Work,Triage"),
        ("me", "m2", "Lunch?", "bob@example.com",
         now - timedelta(hours=3), False, False, ""),
        ("me", "m3", "Re: review", "alice@example.com",
         now - timedelta(hours=5), False, False, "Work"),
        # Already read (excluded).
        ("me", "m4", "Read me", "carol@example.com",
         now - timedelta(hours=2), True, False, "Work"),
        # Tombstoned (excluded).
        ("me", "m5", "Gone", "dave@example.com",
         now - timedelta(hours=2), False, True, ""),
    ]
    with open_catalog(catalog_path) as conn:
        for r in rows:
            conn.execute(
                "INSERT INTO mail_messages (mailbox_upn, message_id, subject, "
                "from_address, received_at, is_read, is_deleted, categories) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                list(r),
            )


def test_digest_default_prints_text(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    now = datetime.now(timezone.utc)
    _seed_catalog(tmp_path / "mail.duckdb", now=now)

    from m365ctl.mail.cli import digest as cli_digest
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls:
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_digest.main(["--config", str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Mail digest" in out
    assert "alice@example.com" in out
    # Read & deleted excluded.
    assert "carol@example.com" not in out
    assert "dave@example.com" not in out


def test_digest_json_emits_ndjson(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    now = datetime.now(timezone.utc)
    _seed_catalog(tmp_path / "mail.duckdb", now=now)

    from m365ctl.mail.cli import digest as cli_digest
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls:
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_digest.main(["--config", str(cfg), "--since", "3d", "--json"])
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 3  # m1, m2, m3 unread alive
    rows = [json.loads(line) for line in out]
    addrs = {r["from_address"] for r in rows}
    assert addrs == {"alice@example.com", "bob@example.com"}


def test_digest_send_to_with_confirm_invokes_send_new(
    tmp_path: Path, capsys
) -> None:
    cfg = _write_config(tmp_path)
    now = datetime.now(timezone.utc)
    _seed_catalog(tmp_path / "mail.duckdb", now=now)

    from m365ctl.mail.cli import digest as cli_digest
    from m365ctl.mail.mutate._common import MailResult
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.digest.GraphClient") as graph_cls, \
         patch("m365ctl.mail.cli.digest.execute_send_new",
               return_value=MailResult(op_id="op-x", status="ok",
                                       after={"sent_at": now.isoformat()})
               ) as send_mock:
        cred_cls.return_value.get_token.return_value = "tok"
        graph = MagicMock()
        graph.get.return_value = {"userPrincipalName": "user@example.com"}
        graph_cls.return_value = graph
        rc = cli_digest.main([
            "--config", str(cfg), "--send-to", "me", "--confirm",
        ])
    assert rc == 0
    assert send_mock.call_count == 1
    op = send_mock.call_args.args[0]
    assert op.action == "mail.send"
    assert op.args["body_type"] == "html"
    assert op.args["to"] == ["user@example.com"]
    assert op.args["subject"].startswith("[Digest]")
    assert op.args["new"] is True
    assert "<h2>" in op.args["body"]


def test_digest_send_to_without_confirm_is_dry_run(
    tmp_path: Path, capsys
) -> None:
    cfg = _write_config(tmp_path)
    now = datetime.now(timezone.utc)
    _seed_catalog(tmp_path / "mail.duckdb", now=now)

    from m365ctl.mail.cli import digest as cli_digest
    with patch("m365ctl.mail.cli.digest.execute_send_new") as send_mock:
        rc = cli_digest.main([
            "--config", str(cfg), "--send-to", "me",
        ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "dry-run" in err.lower() or "would send" in err.lower()
    send_mock.assert_not_called()
