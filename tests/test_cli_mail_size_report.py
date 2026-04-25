"""Tests for `m365ctl mail size-report` CLI."""
from __future__ import annotations

import json
from datetime import datetime, timezone
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


def _seed(catalog_path: Path) -> None:
    from m365ctl.mail.catalog.db import open_catalog
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    rows = [
        ("me", "m1", "Inbox", 30),
        ("me", "m2", "Inbox", 70),
        ("me", "m3", "Archive", 1000),
        ("me", "m4", "Sent Items", 200),
    ]
    with open_catalog(catalog_path) as conn:
        for upn, mid, folder, size in rows:
            conn.execute(
                "INSERT INTO mail_messages (mailbox_upn, message_id, "
                "parent_folder_path, size_estimate, received_at, is_read, "
                "is_deleted) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [upn, mid, folder, size, now, False, False],
            )


def test_size_report_human_print(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    _seed(tmp_path / "mail.duckdb")
    from m365ctl.mail.cli import size_report as cli_size
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls:
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_size.main(["--config", str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Archive" in out
    assert "Inbox" in out
    assert "Sent Items" in out
    # Archive (1000) listed before Inbox (100).
    assert out.index("Archive") < out.index("Inbox")


def test_size_report_json_emits_ndjson(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    _seed(tmp_path / "mail.duckdb")
    from m365ctl.mail.cli import size_report as cli_size
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls:
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_size.main(["--config", str(cfg), "--json"])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 3
    rows = [json.loads(line) for line in lines]
    assert rows[0]["parent_folder_path"] == "Archive"
    assert rows[0]["message_count"] == 1
    assert rows[0]["total_size"] == 1000


def test_size_report_top_5(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    _seed(tmp_path / "mail.duckdb")
    from m365ctl.mail.cli import size_report as cli_size
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls:
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_size.main([
            "--config", str(cfg), "--json", "--top", "2",
        ])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    rows = [json.loads(line) for line in lines]
    assert [r["parent_folder_path"] for r in rows] == ["Archive", "Sent Items"]
