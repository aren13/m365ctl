"""Tests for `m365ctl mail top-senders` CLI."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


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


def _seed(catalog_path: Path) -> datetime:
    from m365ctl.mail.catalog.db import open_catalog
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    rows = [
        ("m1", "alice@example.com", 0),
        ("m2", "alice@example.com", 1),
        ("m3", "alice@example.com", 40),
        ("m4", "bob@example.com", 0),
        ("m5", "bob@example.com", 10),
        ("m6", "carol@example.com", 100),
    ]
    with open_catalog(catalog_path) as conn:
        for mid, addr, days in rows:
            conn.execute(
                "INSERT INTO mail_messages (mailbox_upn, message_id, "
                "from_address, received_at, is_read, is_deleted) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ["me", mid, addr, now - timedelta(days=days), False, False],
            )
    return now


def test_top_senders_human_print(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    _seed(tmp_path / "mail.duckdb")
    from m365ctl.mail.cli import top_senders as cli_ts
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls:
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_ts.main(["--config", str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "alice@example.com" in out
    assert "bob@example.com" in out
    assert "carol@example.com" in out
    # alice (3) listed before bob (2).
    assert out.index("alice") < out.index("bob")


def test_top_senders_json(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    _seed(tmp_path / "mail.duckdb")
    from m365ctl.mail.cli import top_senders as cli_ts
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls:
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_ts.main(["--config", str(cfg), "--json"])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    rows = [json.loads(line) for line in lines]
    assert rows[0]["from_address"] == "alice@example.com"
    assert rows[0]["count"] == 3


def test_top_senders_since_filter(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    _seed(tmp_path / "mail.duckdb")
    from m365ctl.mail.cli import top_senders as cli_ts
    # Freeze "now" so '7d' lines up with the seed data.
    fake_now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return fake_now

    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.top_senders.datetime", _DT):
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_ts.main([
            "--config", str(cfg), "--since", "7d", "--json",
        ])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    rows = [json.loads(line) for line in lines]
    addrs = {r["from_address"]: r["count"] for r in rows}
    # Within last 7 days: alice=2 (m1, m2), bob=1 (m4)
    assert addrs == {"alice@example.com": 2, "bob@example.com": 1}
