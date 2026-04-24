from __future__ import annotations

from pathlib import Path

from m365ctl.mail.catalog.db import open_catalog


def test_open_catalog_creates_parent_dirs(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "mail.duckdb"
    with open_catalog(db_path) as conn:
        (n,) = conn.execute("SELECT COUNT(*) FROM mail_messages").fetchone()
    assert n == 0
    assert db_path.exists()


def test_open_catalog_persists_across_opens(tmp_path: Path) -> None:
    db_path = tmp_path / "mail.duckdb"
    with open_catalog(db_path) as conn:
        conn.execute(
            "INSERT INTO mail_folders (mailbox_upn, folder_id, display_name, "
            "last_seen_at) VALUES (?, ?, ?, ?)",
            ["me", "fld-1", "Inbox", "2026-01-01"],
        )
    with open_catalog(db_path) as conn:
        (n,) = conn.execute("SELECT COUNT(*) FROM mail_folders").fetchone()
    assert n == 1
