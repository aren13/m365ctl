"""Parser + scope-presence smoke for `m365ctl mail whoami`."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from m365ctl.common.auth import GRAPH_SCOPES_DELEGATED
from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.cli import whoami as cli_whoami
from m365ctl.mail.cli.whoami import _REQUIRED_MAIL_SCOPES, build_parser


def test_required_scopes_are_declared():
    for s in _REQUIRED_MAIL_SCOPES:
        assert s in GRAPH_SCOPES_DELEGATED


def test_whoami_parser_accepts_config():
    args = build_parser().parse_args(["--config", "/tmp/cfg.toml"])
    assert args.config == "/tmp/cfg.toml"


def test_whoami_parser_default_config_path():
    args = build_parser().parse_args([])
    assert args.config == "config.toml"


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


def test_whoami_reports_built_catalog(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    with open_catalog(tmp_path / "mail.duckdb") as conn:
        conn.execute(
            "INSERT INTO mail_folders (mailbox_upn, folder_id, display_name, "
            "last_seen_at) VALUES ('me', 'fld-1', 'Inbox', '2026-04-01')"
        )
        conn.execute(
            "INSERT INTO mail_deltas VALUES ('me', 'fld-1', 'd', "
            "'2026-04-01 00:00:00', 'ok')"
        )

    from m365ctl.common.auth import AuthError
    with patch("m365ctl.mail.cli.whoami.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.whoami.AppOnlyCredential") as app_cls, \
         patch("m365ctl.mail.cli.whoami.GraphClient") as graph_cls:
        cred_cls.return_value.get_token.side_effect = AuthError("offline")
        app_cls.side_effect = Exception("no cert")
        gc = MagicMock()
        gc.get.side_effect = AuthError("offline")
        graph_cls.return_value = gc
        rc = cli_whoami.main(["--config", str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Mail catalog:" in out
    assert "1 folders" in out


def test_whoami_reports_missing_catalog(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)

    from m365ctl.common.auth import AuthError
    with patch("m365ctl.mail.cli.whoami.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.whoami.AppOnlyCredential") as app_cls, \
         patch("m365ctl.mail.cli.whoami.GraphClient") as graph_cls:
        cred_cls.return_value.get_token.side_effect = AuthError("offline")
        app_cls.side_effect = Exception("no cert")
        gc = MagicMock()
        gc.get.side_effect = AuthError("offline")
        graph_cls.return_value = gc
        rc = cli_whoami.main(["--config", str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "not built" in out or "(never)" in out
