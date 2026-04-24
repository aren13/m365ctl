from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.cli import search as cli_search


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""
tenant_id = "t"
client_id = "c"
cert_path = "{tmp_path / 'c.pem'}"
cert_public = "{tmp_path / 'p.cer'}"
default_auth = "delegated"
[scope]
allow_drives = ["me"]
allow_mailboxes = ["me"]
[mail]
catalog_path = "{tmp_path / 'mail.duckdb'}"
[logging]
ops_dir = "{tmp_path / 'logs'}"
"""
    )
    return cfg


def _seed_message(tmp_path: Path, **overrides) -> None:
    base = {
        "mailbox_upn": "me",
        "message_id": "m1",
        "internet_message_id": "<m1@example.com>",
        "conversation_id": None,
        "parent_folder_id": "fld-inbox",
        "parent_folder_path": "Inbox",
        "subject": "Quarterly review",
        "from_address": "alice@example.com",
        "from_name": "Alice",
        "to_addresses": "me@example.com",
        "received_at": datetime(2026, 4, 1, tzinfo=timezone.utc),
        "sent_at": None,
        "is_read": False,
        "is_draft": False,
        "has_attachments": False,
        "importance": "normal",
        "flag_status": "notFlagged",
        "categories": "",
        "inference_class": "focused",
        "body_preview": "Q1 numbers attached",
        "web_link": "",
        "size_estimate": 0,
        "is_deleted": False,
        "last_seen_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    with open_catalog(tmp_path / "mail.duckdb") as conn:
        conn.execute(
            "INSERT INTO mail_messages VALUES ($mailbox_upn, $message_id, "
            "$internet_message_id, $conversation_id, $parent_folder_id, "
            "$parent_folder_path, $subject, $from_address, $from_name, "
            "$to_addresses, $received_at, $sent_at, $is_read, $is_draft, "
            "$has_attachments, $importance, $flag_status, $categories, "
            "$inference_class, $body_preview, $web_link, $size_estimate, "
            "$is_deleted, $last_seen_at)",
            base,
        )


def test_search_local_subject_match(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    _seed_message(tmp_path)
    rc = cli_search.main(["--config", str(cfg), "--local", "quarterly"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Quarterly review" in out
    assert "alice@example.com" in out


def test_search_local_no_hits_returns_zero(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    _seed_message(tmp_path)
    rc = cli_search.main(["--config", str(cfg), "--local", "nothing-matches"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "(no local hits)" in out


def test_search_local_empty_catalog_warns(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    rc = cli_search.main(["--config", str(cfg), "--local", "anything"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "catalog empty" in err.lower()


def test_search_local_excludes_deleted(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    _seed_message(tmp_path, message_id="dead", subject="ghost", is_deleted=True)
    rc = cli_search.main(["--config", str(cfg), "--local", "ghost"])
    out = capsys.readouterr().out
    assert "ghost" not in out
