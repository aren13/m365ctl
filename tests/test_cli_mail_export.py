"""CLI tests for `mail export {message, folder, mailbox, attachments}` (Phase 11 G4)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from m365ctl.mail.cli import export as cli_export


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""
tenant_id    = "00000000-0000-0000-0000-000000000000"
client_id    = "11111111-1111-1111-1111-111111111111"
cert_path    = "{tmp_path / 'c.pem'}"
cert_public  = "{tmp_path / 'p.cer'}"
default_auth = "delegated"

[scope]
allow_drives    = ["me"]
allow_mailboxes = ["me"]

[catalog]
path = "{tmp_path / 'cat.duckdb'}"

[mail]
catalog_path = "{tmp_path / 'mail.duckdb'}"

[logging]
ops_dir = "{tmp_path / 'logs'}"
""".lstrip()
    )
    return cfg


class _Patched:
    """Stub credentials + GraphClient at construction site (cli._common)."""

    def __enter__(self):
        self._patches = [
            patch("m365ctl.mail.cli._common.DelegatedCredential"),
            patch("m365ctl.mail.cli._common.AppOnlyCredential"),
            patch("m365ctl.mail.cli.export.GraphClient"),
        ]
        self.cred_cls = self._patches[0].__enter__()
        self.app_cred_cls = self._patches[1].__enter__()
        self.graph_cls = self._patches[2].__enter__()

        self.cred_cls.return_value.get_token.return_value = "tok"
        self.app_cred_cls.return_value.get_token.return_value = "tok"
        self.graph_cls.return_value = MagicMock()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.__exit__(*exc)


def test_message_calls_export_message_to_eml(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    out = tmp_path / "msg.eml"
    with _Patched(), \
         patch("m365ctl.mail.cli.export.export_message_to_eml",
               return_value=out) as m:
        rc = cli_export.main([
            "--config", str(cfg),
            "message", "msg-1", "--out", str(out),
        ])
    assert rc == 0
    assert m.call_count == 1
    kwargs = m.call_args.kwargs
    assert kwargs["message_id"] == "msg-1"
    assert kwargs["out_path"] == out
    assert kwargs["mailbox_spec"] == "me"
    assert kwargs["auth_mode"] == "delegated"


def test_folder_resolves_path_and_calls_export_folder_to_mbox(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    out = tmp_path / "Inbox.mbox"
    with _Patched(), \
         patch("m365ctl.mail.cli.export.resolve_folder_path",
               return_value="fld-resolved") as resolve_mock, \
         patch("m365ctl.mail.cli.export.export_folder_to_mbox",
               return_value=(3, None, None)) as m:
        rc = cli_export.main([
            "--config", str(cfg),
            "folder", "Inbox", "--out", str(out),
        ])
    assert rc == 0
    resolve_mock.assert_called_once()
    assert m.call_count == 1
    kwargs = m.call_args.kwargs
    assert kwargs["folder_id"] == "fld-resolved"
    assert kwargs["folder_path"] == "Inbox"
    assert kwargs["out_path"] == out


def test_mailbox_calls_export_mailbox(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    out_dir = tmp_path / "export-out"
    fake_manifest = MagicMock()
    fake_manifest.folders = {}
    with _Patched(), \
         patch("m365ctl.mail.cli.export.export_mailbox",
               return_value=fake_manifest) as m:
        rc = cli_export.main([
            "--config", str(cfg),
            "mailbox", "--out-dir", str(out_dir),
        ])
    assert rc == 0
    assert m.call_count == 1
    kwargs = m.call_args.kwargs
    assert kwargs["out_dir"] == out_dir
    assert kwargs["mailbox_spec"] == "me"
    assert kwargs["mailbox_upn"] == "me"
    assert kwargs["auth_mode"] == "delegated"


def test_attachments_default_skips_inline(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    out_dir = tmp_path / "atts"
    with _Patched(), \
         patch("m365ctl.mail.cli.export.export_attachments",
               return_value=[]) as m:
        rc = cli_export.main([
            "--config", str(cfg),
            "attachments", "msg-1", "--out-dir", str(out_dir),
        ])
    assert rc == 0
    assert m.call_count == 1
    kwargs = m.call_args.kwargs
    assert kwargs["message_id"] == "msg-1"
    assert kwargs["out_dir"] == out_dir
    assert kwargs["include_inline"] is False


def test_attachments_include_inline_flag(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    out_dir = tmp_path / "atts"
    with _Patched(), \
         patch("m365ctl.mail.cli.export.export_attachments",
               return_value=[]) as m:
        rc = cli_export.main([
            "--config", str(cfg),
            "attachments", "msg-1", "--out-dir", str(out_dir),
            "--include-inline",
        ])
    assert rc == 0
    kwargs = m.call_args.kwargs
    assert kwargs["include_inline"] is True


def test_message_without_out_returns_2(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    with pytest.raises(SystemExit) as ei:
        cli_export.main([
            "--config", str(cfg),
            "message", "msg-1",
        ])
    assert ei.value.code == 2
    err = capsys.readouterr().err
    assert "--out" in err or "required" in err.lower()
