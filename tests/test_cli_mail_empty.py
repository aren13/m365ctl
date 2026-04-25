"""Tests for `m365ctl mail empty <folder>` CLI."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from m365ctl.mail.models import Folder


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


def _folder(display_name: str, total: int, path: str | None = None) -> Folder:
    return Folder(
        id="FOLDER-ID",
        mailbox_upn="me",
        display_name=display_name,
        parent_id=None,
        path=path or display_name,
        total_items=total,
        unread_items=0,
        child_folder_count=0,
        well_known_name=None,
    )


def test_empty_help_opens_with_irreversible_warning() -> None:
    from m365ctl.mail.cli.empty import build_parser
    help_text = build_parser().format_help()
    assert "IRREVERSIBLE" in help_text
    assert "mail delete" in help_text or "mail-delete" in help_text


def test_empty_without_confirm_returns_2(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import empty as cli_empty
    rc = cli_empty.main(["--config", str(cfg), "Junk"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--confirm" in err


def test_empty_zero_total_exits_0_without_executor(
    tmp_path: Path, capsys,
) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import empty as cli_empty
    fake_executor = MagicMock()
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.empty.GraphClient"), \
         patch("m365ctl.mail.cli.empty.resolve_folder_path", return_value="FID"), \
         patch(
             "m365ctl.mail.cli.empty.get_folder",
             return_value=_folder("Junk", total=0),
         ), \
         patch("m365ctl.mail.cli.empty.execute_empty_folder", fake_executor):
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_empty.main(["--config", str(cfg), "Junk", "--confirm"])
    assert rc == 0
    assert fake_executor.call_count == 0
    err = capsys.readouterr().err
    assert "(folder is empty)" in err


def test_empty_common_folder_without_flag_returns_1(
    tmp_path: Path, capsys,
) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import empty as cli_empty
    fake_executor = MagicMock()
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.empty.GraphClient"), \
         patch("m365ctl.mail.cli.empty.resolve_folder_path", return_value="FID"), \
         patch(
             "m365ctl.mail.cli.empty.get_folder",
             return_value=_folder("Inbox", total=42),
         ), \
         patch("m365ctl.mail.cli.empty.execute_empty_folder", fake_executor):
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_empty.main(["--config", str(cfg), "Inbox", "--confirm"])
    assert rc == 1
    assert fake_executor.call_count == 0
    err = capsys.readouterr().err
    assert "Inbox" in err
    assert "--unsafe-common-folder" in err


def test_empty_common_folder_with_flag_and_yes_calls_executor(
    tmp_path: Path,
) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import empty as cli_empty
    fake_result = MagicMock(status="ok", error=None, after={"purged_count": 42})
    fake_executor = MagicMock(return_value=fake_result)
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.empty.GraphClient"), \
         patch("m365ctl.mail.cli.empty.resolve_folder_path", return_value="FID"), \
         patch(
             "m365ctl.mail.cli.empty.get_folder",
             return_value=_folder("Inbox", total=42),
         ), \
         patch("m365ctl.mail.cli.empty._tty_prompt", lambda _m: "YES"), \
         patch("m365ctl.mail.cli.empty.execute_empty_folder", fake_executor):
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_empty.main([
            "--config", str(cfg), "Inbox",
            "--confirm", "--unsafe-common-folder",
        ])
    assert rc == 0
    assert fake_executor.call_count == 1
    op = fake_executor.call_args.args[0]
    assert op.action == "mail.empty.folder"
    assert op.args["folder_id"] == "FID"


def test_empty_non_common_under_1000_with_yes_calls_executor(
    tmp_path: Path,
) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import empty as cli_empty
    fake_result = MagicMock(status="ok", error=None, after={"purged_count": 5})
    fake_executor = MagicMock(return_value=fake_result)
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.empty.GraphClient"), \
         patch("m365ctl.mail.cli.empty.resolve_folder_path", return_value="FID"), \
         patch(
             "m365ctl.mail.cli.empty.get_folder",
             return_value=_folder("Junk", total=5),
         ), \
         patch("m365ctl.mail.cli.empty._tty_prompt", lambda _m: "YES"), \
         patch("m365ctl.mail.cli.empty.execute_empty_folder", fake_executor):
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_empty.main(["--config", str(cfg), "Junk", "--confirm"])
    assert rc == 0
    assert fake_executor.call_count == 1


def test_empty_non_common_over_1000_requires_exact_phrase(
    tmp_path: Path,
) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import empty as cli_empty
    fake_result = MagicMock(
        status="ok", error=None, after={"purged_count": 1234},
    )
    fake_executor = MagicMock(return_value=fake_result)
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.empty.GraphClient"), \
         patch("m365ctl.mail.cli.empty.resolve_folder_path", return_value="FID"), \
         patch(
             "m365ctl.mail.cli.empty.get_folder",
             return_value=_folder("Junk", total=1234),
         ), \
         patch(
             "m365ctl.mail.cli.empty._tty_prompt",
             lambda _m: "YES DELETE 1234",
         ), \
         patch("m365ctl.mail.cli.empty.execute_empty_folder", fake_executor):
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_empty.main(["--config", str(cfg), "Junk", "--confirm"])
    assert rc == 0
    assert fake_executor.call_count == 1


def test_empty_over_1000_wrong_phrase_aborts(
    tmp_path: Path, capsys,
) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import empty as cli_empty
    fake_executor = MagicMock()
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.empty.GraphClient"), \
         patch("m365ctl.mail.cli.empty.resolve_folder_path", return_value="FID"), \
         patch(
             "m365ctl.mail.cli.empty.get_folder",
             return_value=_folder("Junk", total=1234),
         ), \
         patch("m365ctl.mail.cli.empty._tty_prompt", lambda _m: "YES"), \
         patch("m365ctl.mail.cli.empty.execute_empty_folder", fake_executor):
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_empty.main(["--config", str(cfg), "Junk", "--confirm"])
    assert rc == 1
    assert fake_executor.call_count == 0
    err = capsys.readouterr().err
    assert "aborted" in err.lower()


def test_empty_no_tty_returns_1(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import empty as cli_empty

    def boom(_msg: str) -> str:
        raise IOError("no tty")

    fake_executor = MagicMock()
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.empty.GraphClient"), \
         patch("m365ctl.mail.cli.empty.resolve_folder_path", return_value="FID"), \
         patch(
             "m365ctl.mail.cli.empty.get_folder",
             return_value=_folder("Junk", total=5),
         ), \
         patch("m365ctl.mail.cli.empty._tty_prompt", boom), \
         patch("m365ctl.mail.cli.empty.execute_empty_folder", fake_executor):
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_empty.main(["--config", str(cfg), "Junk", "--confirm"])
    assert rc == 1
    assert fake_executor.call_count == 0
    err = capsys.readouterr().err
    assert "requires TTY confirm" in err
    assert "irreversible" in err
