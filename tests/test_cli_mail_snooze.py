"""Tests for `m365ctl mail snooze` CLI."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
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


def test_snooze_until_iso_dispatches_two_ops(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import snooze as cli_snooze
    fake_result = MagicMock(status="ok")
    fake_executor = MagicMock(return_value=fake_result)
    fake_executors = {
        "mail.move": fake_executor,
        "mail.categorize": fake_executor,
    }
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.snooze.GraphClient"), \
         patch("m365ctl.mail.cli.snooze._EXECUTORS", fake_executors):
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_snooze.main([
            "--config", str(cfg), "MID-1",
            "--until", "2026-05-01", "--confirm",
        ])
    assert rc == 0
    # Two calls: one move, one categorize.
    assert fake_executor.call_count == 2
    actions = [c.args[0].action for c in fake_executor.call_args_list]
    assert sorted(actions) == ["mail.categorize", "mail.move"]


def test_snooze_until_relative_resolves_date(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import snooze as cli_snooze
    fake_result = MagicMock(status="ok")
    fake_executor = MagicMock(return_value=fake_result)
    fake_executors = {
        "mail.move": fake_executor,
        "mail.categorize": fake_executor,
    }
    fake_now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return fake_now

    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.snooze.GraphClient"), \
         patch("m365ctl.mail.cli.snooze._EXECUTORS", fake_executors), \
         patch("m365ctl.mail.cli.snooze.datetime", _DT):
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_snooze.main([
            "--config", str(cfg), "MID-1",
            "--until", "5d", "--confirm",
        ])
    assert rc == 0
    # Inspect emitted to_folder.
    move_calls = [
        c for c in fake_executor.call_args_list
        if c.args[0].action == "mail.move"
    ]
    expected = (fake_now + timedelta(days=5)).date().isoformat()
    assert move_calls[0].args[0].args["to_folder"] == f"Deferred/{expected}"


def test_snooze_missing_confirm_returns_2(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import snooze as cli_snooze
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls:
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_snooze.main([
            "--config", str(cfg), "MID-1", "--until", "2026-05-01",
        ])
    assert rc == 2


def _seed_folders_and_messages(catalog_path: Path) -> None:
    from m365ctl.mail.catalog.db import open_catalog
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    folders = [
        ("me", "f-inbox", "Inbox"),
        ("me", "f-due-1", "Deferred/2026-04-20"),
        ("me", "f-due-2", "Deferred/2026-04-25"),
        ("me", "f-future", "Deferred/2026-06-01"),
    ]
    msgs = [
        ("me", "m-due-1a", "f-due-1", "Deferred/2026-04-20",
         "Snooze/2026-04-20"),
        ("me", "m-due-2a", "f-due-2", "Deferred/2026-04-25",
         "Snooze/2026-04-25"),
        ("me", "m-future", "f-future", "Deferred/2026-06-01",
         "Snooze/2026-06-01"),
    ]
    with open_catalog(catalog_path) as conn:
        for upn, fid, path in folders:
            conn.execute(
                "INSERT INTO mail_folders (mailbox_upn, folder_id, path) "
                "VALUES (?, ?, ?)",
                [upn, fid, path],
            )
        for upn, mid, fid, path, cats in msgs:
            conn.execute(
                "INSERT INTO mail_messages (mailbox_upn, message_id, "
                "parent_folder_id, parent_folder_path, categories, "
                "received_at, is_read, is_deleted) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [upn, mid, fid, path, cats, now, False, False],
            )


def test_snooze_process_moves_due_back(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    _seed_folders_and_messages(tmp_path / "mail.duckdb")
    from m365ctl.mail.cli import snooze as cli_snooze
    fake_result = MagicMock(status="ok")
    fake_executor = MagicMock(return_value=fake_result)
    fake_executors = {
        "mail.move": fake_executor,
        "mail.categorize": fake_executor,
    }
    fake_now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return fake_now

    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.snooze.GraphClient"), \
         patch("m365ctl.mail.cli.snooze._EXECUTORS", fake_executors), \
         patch("m365ctl.mail.cli.snooze.datetime", _DT):
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_snooze.main([
            "--config", str(cfg), "--process", "--confirm",
        ])
    assert rc == 0
    # 2 due messages * (move + categorize) = 4 calls.
    assert fake_executor.call_count == 4
    move_targets = [
        c.args[0].args["to_folder"]
        for c in fake_executor.call_args_list
        if c.args[0].action == "mail.move"
    ]
    assert move_targets == ["Inbox", "Inbox"]
    # Future folder's message must not be touched.
    moved_ids = [
        c.args[0].item_id
        for c in fake_executor.call_args_list
        if c.args[0].action == "mail.move"
    ]
    assert "m-future" not in moved_ids
    assert sorted(moved_ids) == ["m-due-1a", "m-due-2a"]
    # Verify _ = silence ref-unused
    _ = date
