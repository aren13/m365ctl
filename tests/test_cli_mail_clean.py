"""Tests for `m365ctl mail clean` CLI."""
from __future__ import annotations

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
purged_dir = "{tmp_path / 'purged'}"
"""
    )
    return cfg


def test_clean_help_opens_with_irreversible_warning() -> None:
    from m365ctl.mail.cli.clean import build_parser
    help_text = build_parser().format_help()
    assert "IRREVERSIBLE" in help_text
    assert "mail delete" in help_text or "mail-delete" in help_text


def test_clean_without_confirm_returns_2(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import clean as cli_clean
    rc = cli_clean.main(["--config", str(cfg), "MID-1"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--confirm" in err


def test_clean_with_confirm_no_tty_returns_1(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import clean as cli_clean

    def boom(_msg: str) -> str:
        raise IOError("no tty")

    with patch("m365ctl.mail.cli.clean._tty_prompt", boom):
        rc = cli_clean.main(["--config", str(cfg), "MID-1", "--confirm"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "requires TTY confirm" in err
    assert "irreversible" in err


def test_clean_message_id_with_yes_calls_executor(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import clean as cli_clean
    fake_result = MagicMock(status="ok", error=None, after={})
    fake_executor = MagicMock(return_value=fake_result)
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.clean.GraphClient"), \
         patch("m365ctl.mail.cli.clean._tty_prompt", lambda _m: "YES"), \
         patch("m365ctl.mail.cli.clean.execute_hard_delete", fake_executor):
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_clean.main(["--config", str(cfg), "MID-1", "--confirm"])
    assert rc == 0
    assert fake_executor.call_count == 1
    op = fake_executor.call_args.args[0]
    assert op.action == "mail.delete.hard"
    assert op.args["message_id"] == "MID-1"
    assert "purged_dir" in fake_executor.call_args.kwargs


def test_clean_message_id_with_wrong_phrase_aborts(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import clean as cli_clean
    fake_executor = MagicMock()
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.clean.GraphClient"), \
         patch("m365ctl.mail.cli.clean._tty_prompt", lambda _m: "yes"), \
         patch("m365ctl.mail.cli.clean.execute_hard_delete", fake_executor):
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_clean.main(["--config", str(cfg), "MID-1", "--confirm"])
    assert rc == 1
    assert fake_executor.call_count == 0
    err = capsys.readouterr().err
    assert "aborted" in err.lower()


def test_clean_recycle_bin_routes_to_empty_recycle_executor(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import clean as cli_clean
    fake_result = MagicMock(status="ok", error=None, after={"purged_count": 3})
    fake_executor = MagicMock(return_value=fake_result)
    fake_hard = MagicMock()
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.clean.GraphClient"), \
         patch("m365ctl.mail.cli.clean._tty_prompt", lambda _m: "YES"), \
         patch("m365ctl.mail.cli.clean.execute_empty_recycle_bin", fake_executor), \
         patch("m365ctl.mail.cli.clean.execute_hard_delete", fake_hard):
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_clean.main(["--config", str(cfg), "recycle-bin", "--confirm"])
    assert rc == 0
    assert fake_executor.call_count == 1
    assert fake_hard.call_count == 0
    op = fake_executor.call_args.args[0]
    assert op.action == "mail.empty.recycle-bin"
