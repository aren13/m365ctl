from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


from m365ctl.mail.catalog.crawl import CrawlOutcome
from m365ctl.mail.cli import catalog as cli_catalog


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


def test_catalog_refresh_invokes_refresh_mailbox(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    fake_outcomes = [
        CrawlOutcome("fld-inbox", "Inbox", 5, "delta-1", "ok"),
        CrawlOutcome("fld-sent", "Sent Items", 2, "delta-2", "ok"),
    ]

    with patch("m365ctl.mail.cli.catalog.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.catalog.GraphClient") as graph_cls, \
         patch("m365ctl.mail.cli.catalog.refresh_mailbox",
               return_value=fake_outcomes) as refresh_mock:
        cred_cls.return_value.get_token.return_value = "tok"
        graph_cls.return_value = MagicMock()
        rc = cli_catalog.main(["refresh", "--config", str(cfg)])
    assert rc == 0
    assert refresh_mock.call_count == 1
    out = capsys.readouterr().out
    assert "Inbox" in out and "5" in out
    assert "Sent Items" in out and "2" in out


def test_catalog_refresh_with_folder_resolves_path(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    with patch("m365ctl.mail.cli.catalog.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.catalog.GraphClient") as graph_cls, \
         patch("m365ctl.mail.cli.catalog.resolve_folder_path",
               return_value="fld-resolved") as resolve_mock, \
         patch("m365ctl.mail.cli.catalog.refresh_mailbox",
               return_value=[]) as refresh_mock:
        cred_cls.return_value.get_token.return_value = "tok"
        graph_cls.return_value = MagicMock()
        rc = cli_catalog.main([
            "refresh", "--config", str(cfg), "--folder", "Inbox/Triage",
        ])
    assert rc == 0
    resolve_mock.assert_called_once()
    kwargs = refresh_mock.call_args.kwargs
    assert kwargs["folder_filter"] == "fld-resolved"


def test_catalog_refresh_passes_max_rounds(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    with patch("m365ctl.mail.cli.catalog.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.catalog.GraphClient") as graph_cls, \
         patch("m365ctl.mail.cli.catalog.refresh_mailbox",
               return_value=[]) as refresh_mock:
        cred_cls.return_value.get_token.return_value = "tok"
        graph_cls.return_value = MagicMock()
        rc = cli_catalog.main([
            "refresh", "--config", str(cfg), "--max-rounds", "2",
        ])
    assert rc == 0
    kwargs = refresh_mock.call_args.kwargs
    assert kwargs["max_rounds"] == 2


def test_catalog_refresh_marks_truncated_in_output(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    fake_outcomes = [
        CrawlOutcome(
            "fld-inbox", "Inbox", 5, "delta-1", "ok", truncated=True,
        ),
    ]
    with patch("m365ctl.mail.cli.catalog.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.catalog.GraphClient") as graph_cls, \
         patch("m365ctl.mail.cli.catalog.refresh_mailbox",
               return_value=fake_outcomes):
        cred_cls.return_value.get_token.return_value = "tok"
        graph_cls.return_value = MagicMock()
        rc = cli_catalog.main(["refresh", "--config", str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[truncated" in out


def test_catalog_status_prints_summary(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    # Pre-create the catalog with a row so status has something to print.
    from m365ctl.mail.catalog.db import open_catalog
    with open_catalog(tmp_path / "mail.duckdb") as conn:
        conn.execute(
            "INSERT INTO mail_folders (mailbox_upn, folder_id, display_name, "
            "last_seen_at) VALUES ('me', 'fld-1', 'Inbox', '2026-04-01')"
        )
        conn.execute(
            "INSERT INTO mail_deltas (mailbox_upn, folder_id, delta_link, "
            "last_refreshed_at, last_status) VALUES "
            "('me', 'fld-1', 'd', '2026-04-01', 'ok')"
        )

    rc = cli_catalog.main(["status", "--config", str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Mail catalog" in out
    assert "Folders" in out and "1" in out
