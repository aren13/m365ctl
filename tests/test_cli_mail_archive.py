"""Tests for `m365ctl mail archive` CLI."""
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


def _seed_old_messages(catalog_path: Path, *, now: datetime) -> None:
    from m365ctl.mail.catalog.db import open_catalog
    rows = [
        # Old Inbox messages (archive candidates).
        ("me", "old1", "Old one", "alice@example.com",
         now - timedelta(days=120), "Inbox"),
        ("me", "old2", "Old two", "bob@example.com",
         now - timedelta(days=200), "Inbox"),
        # Recent (excluded).
        ("me", "new1", "Fresh", "alice@example.com",
         now - timedelta(days=10), "Inbox"),
    ]
    with open_catalog(catalog_path) as conn:
        for upn, mid, subject, from_addr, recv, folder in rows:
            conn.execute(
                "INSERT INTO mail_messages (mailbox_upn, message_id, subject, "
                "from_address, received_at, parent_folder_path, is_read, "
                "is_deleted) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [upn, mid, subject, from_addr, recv, folder, False, False],
            )


def test_archive_plan_out_writes_plan(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    _seed_old_messages(tmp_path / "mail.duckdb", now=now)

    plan_out = tmp_path / "plan.json"
    from m365ctl.mail.cli import archive as cli_archive
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls:
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_archive.main([
            "--config", str(cfg),
            "--older-than-days", "90",
            "--folder", "Inbox",
            "--plan-out", str(plan_out),
        ])
    assert rc == 0
    payload = json.loads(plan_out.read_text())
    ids = [op["item_id"] for op in payload["operations"]]
    assert set(ids) == {"old1", "old2"}
    for op in payload["operations"]:
        assert op["action"] == "mail.move"
        assert op["args"]["to_folder"].startswith("Archive/")


def test_archive_missing_both_flags_returns_2(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import archive as cli_archive
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls:
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_archive.main([
            "--config", str(cfg),
            "--older-than-days", "90",
            "--folder", "Inbox",
        ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--plan-out" in err and "--confirm" in err


def test_archive_confirm_dispatches_via_executors(
    tmp_path: Path, capsys
) -> None:
    cfg = _write_config(tmp_path)
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    _seed_old_messages(tmp_path / "mail.duckdb", now=now)

    from m365ctl.mail.cli import archive as cli_archive
    fake_result = MagicMock()
    fake_result.status = "ok"
    fake_executor = MagicMock(return_value=fake_result)
    fake_executors = {"mail.move": fake_executor}
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.archive.GraphClient"), \
         patch("m365ctl.mail.cli.archive._EXECUTORS", fake_executors):
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_archive.main([
            "--config", str(cfg),
            "--older-than-days", "90",
            "--folder", "Inbox",
            "--confirm",
        ])
    assert rc == 0
    # Two old messages → two dispatch calls.
    assert fake_executor.call_count == 2
    # Each call passes op + the named kwargs.
    for call in fake_executor.call_args_list:
        op = call.args[0]
        assert op.action == "mail.move"
        assert "cfg" in call.kwargs
        assert "graph" in call.kwargs
        assert "logger" in call.kwargs
